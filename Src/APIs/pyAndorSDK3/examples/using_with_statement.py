from pyAndorSDK3 import AndorSDK3
from pyAndorSDK3 import CameraException
from pyAndorSDK3 import ErrorCodes

print("Connecting to camera")

sdk3 = AndorSDK3()

# camera will be open within the with statememt
with sdk3.GetCamera(0) as cam:
    print(cam.SerialNumber)
    cam.ExposureTime = 0.01
    cam.FrameCount = 1
    acq = cam.acquire()

# trying to use the camera after exiting the with will result in an exception
# to to the camera automatically closing after exuting the with
try:
    print(cam.SerialNumber)
except CameraException as e:
    if e.err_code == ErrorCodes.AT_ERR_INVALIDHANDLE:
        print("Can no longer use camera after exiting 'with' since camera" +
              " has been closed")
