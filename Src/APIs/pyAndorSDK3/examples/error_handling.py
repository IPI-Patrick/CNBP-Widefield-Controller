from pyAndorSDK3 import AndorSDK3
from pyAndorSDK3 import CameraException
from pyAndorSDK3 import ErrorCodes
import numpy as np

print("Connecting to camera")

sdk3 = AndorSDK3()
cam = sdk3.GetCamera(0)
print(cam.SerialNumber)


# example 1:  values out of range
try:
    cam.ExposureTime = -1  # example value for demonstration
except CameraException as e:
    if e.err_code == ErrorCodes.AT_ERR_OUTOFRANGE:
        # do recovery code
        pass
    else:
        # got unexpected error
        raise e


# example 2: out of range error is also produced for invalid enum options
try:
    cam.PixelEncoding = "InvalidOption"
except CameraException as e:
    if e.err_code == ErrorCodes.AT_ERR_OUTOFRANGE:
        # do recovery code
        pass
    else:
        # got unexpected error
        raise e


# example 3: testing if a feature is implemented
try:
    x = cam.NotImplementedFeature
except AttributeError as e:
    # 'Camera' object raises AttributeError for features not implemented
    # do recovery code or just raise e
    pass

# example 4: Handling Timeout from WaitBuffer while waiting for Image data

imgsize = cam.ImageSizeBytes
buf = np.empty((imgsize,), dtype='B')
cam.queue(buf, imgsize)
cam.AcquisitionStart()

try:
    acq = cam.wait_buffer(1000)
except CameraException as e:
    if e.err_code == ErrorCodes.AT_ERR_TIMEDOUT:
        # got timeout while waiting for image
        # do recovery code or just raise e
        pass
finally:
    cam.AcquisitionStop()
    cam.flush()
