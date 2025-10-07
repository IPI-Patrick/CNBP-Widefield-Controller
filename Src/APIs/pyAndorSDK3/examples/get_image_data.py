from pyAndorSDK3 import AndorSDK3

print("\nConnecting to camera...")

sdk3 = AndorSDK3()
cam = sdk3.GetCamera(0)
print(cam.SerialNumber)

print("\nGetting image data...")
acq = cam.acquire(timeout=20000)
print("Performed single acqition", acq.image)


cam.CycleMode = "Fixed"
cam.FrameCount = 5
# not passing in frame_count when using
# Fixed CycleMode (i.e. using FrameCount Feature)
acqs = cam.acquire_series(timeout=20000)
print("{} CycleMode Expecting {} images - Acquired {} images".format(
    cam.CycleMode, cam.FrameCount, len(acqs)))


cam.CycleMode = "Fixed"
# using frame_count param with Fixed CycleMode
# Note: FrameCount is set by acquire_series when in Fixed CycleMode
acqs = cam.acquire_series(frame_count=10, timeout=20000)
print("{} CycleMode Expecting {} images - Acquired {} images".format(
    cam.CycleMode, cam.FrameCount, len(acqs)))


cam.CycleMode = "Continuous"
fc = 20
# using frame_count param with Continuous CycleMode
acqs = cam.acquire_series(frame_count=fc, timeout=20000)
print("{} CycleMode Expecting {} images - Acquired {} images".format(
    cam.CycleMode, fc, len(acqs)))
