from pyAndorSDK3 import AndorSDK3
from collections import deque
import numpy as np


def custom_acquire_series_circular(cam, frame_count, buffer_count=5):

    timeout = 1000
    cam.TriggerMode == "Software"
    cam.CycleMode = "Continuous"
    if cam.CycleMode == "Fixed":
        cam.FrameCount = frame_count

    imgsize = cam.ImageSizeBytes
    for _ in range(0, buffer_count):
        buf = np.empty((imgsize,), dtype='B')
        cam.queue(buf, imgsize)

    software_trigger = cam.TriggerMode == "Software"
    frame = None
    acqs = deque()
    try:
        cam.AcquisitionStart()
        frame = 0
        while(True):
            if software_trigger:
                cam.SoftwareTrigger()

            acq = cam.wait_buffer(timeout)
            if frame >= buffer_count:
                acqs.popleft()
            acqs.append(acq)
            cam.queue(acq._np_data, imgsize)

            frame += 1
            percent = int((frame/frame_count)*100)
            print("{}% complete series".format(percent), end="\r")
            if frame == frame_count:
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
    return acqs


print("\nConnecting to camera...")

sdk3 = AndorSDK3()
cam = sdk3.GetCamera(0)
print(cam.SerialNumber)

cam.AOIHeight = 1000
cam.AOIWidth = 1000
cam.ExposureTime = cam.min_ExposureTime

acqs = custom_acquire_series_circular(cam, 100)
for acq in acqs:
    print(acq.image)

print("Done")
