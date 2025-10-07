import numpy as np
from pyAndorSDK3 import AndorSDK3
from collections import deque


def process_image(acquisition):
    raw_img = acquisition._np_data
    raw_img + 1
    acquisition._np_data = raw_img
    return acquisition


def custom_acquire_series(cam, frame_count):
    timeout = 1000
    cam.TriggerMode == "Software"
    cam.CycleMode == "Fixed"
    cam.FrameCount = frame_count

    imgsize = cam.ImageSizeBytes
    for _ in range(0, frame_count):
        buf = np.empty((imgsize,), dtype='B')
        cam.queue(buf, imgsize)

    series = deque()
    frame = None
    try:
        cam.AcquisitionStart()
        frame = 0
        while(True):
            cam.SoftwareTrigger()
            acq = cam.wait_buffer(timeout)
            acq = process_image(acq)
            series.append(acq)

            frame += 1
            percent = int((frame/frame_count)*100)
            print("{}% complete series".format(percent), end="\r")
            if frame >= frame_count:
                print()
                break
    except Exception as e:
        if frame is not None:
            print()
            print("Error on frame "+str(frame))
        cam.AcquisitionStop()
        cam.flush()
        raise e
    cam.AcquisitionStop()
    cam.flush()
    return list(series)


print("\nConnecting to camera...")

sdk3 = AndorSDK3()
cam = sdk3.GetCamera(0)
print(cam.SerialNumber)

cam.AOIHeight = 1000
cam.AOIWidth = 1000
cam.ExposureTime = cam.min_ExposureTime
cam.FrameRate = cam.max_FrameRate

acqs = custom_acquire_series(cam, 100)

print("Done")
