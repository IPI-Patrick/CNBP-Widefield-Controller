from pyAndorSDK3 import AndorSDK3
import numpy as np


print("\nConnecting to camera...")

sdk3 = AndorSDK3()
cam = sdk3.GetCamera(0)
print(cam.SerialNumber)

cam.AOIHeight = 1000
cam.AOIWidth = 1000
cam.ExposureTime = cam.min_ExposureTime
cam.FrameRate = cam.max_FrameRate

# queuing buffer
imgsize = cam.ImageSizeBytes
buf = np.empty((imgsize,), dtype='B')
cam.queue(buf, imgsize)

# start
cam.AcquisitionStart()

# wait
acq = cam.wait_buffer(1000)

# stop
cam.AcquisitionStop()
cam.flush()

print("Done")
