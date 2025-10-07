from pyAndorSDK3 import AndorSDK3

print("Connecting to camera")

sdk3 = AndorSDK3()
cam = sdk3.GetCamera(0)

# It is possible in some cases the camera can acquire faster than the interface
# can transfer image data to the PC
# When this happens the camera storage for image data can fill up and once full
# the camera will stop acquiring images and this may result in receiving less
# images than expected and WaitBuffer will produce a timeout error

# To help avoid this it may be needed to use MaxInterfaceTransferRate feature
# Example:

# Setting up for large data sizes
cam.PixelEncoding = "Mono32"
cam.MetadataEnable = True
try:
    cam.MetadataIRIG = True
except AttributeError:
    pass
cam.AOIHeight = cam.max_AOIHeight
cam.AOIWidth = cam.max_AOIWidth
cam.ExposureTime = cam.min_ExposureTime


max_fps = cam.max_FrameRate
max_rate = cam.MaxInterfaceTransferRate
print("Max FPS = {}  MaxInterfaceTrasnferRate = {}".format(max_fps, max_rate))
if max_rate < max_fps:
    print("Max FPS too high - setting FrameRate to MaxInterfaceTrasnferRate")
    cam.FrameRate = max_rate
else:
    print("Max FPS within MaxInterfaceTransferRate - setting FrameRate to max FPS")
    cam.FrameRate = max_fps
