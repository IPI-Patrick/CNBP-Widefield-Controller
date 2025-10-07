from pyAndorSDK3 import AndorSDK3

print("\nConnecting to camera...")

sdk3 = AndorSDK3()
cam = sdk3.GetCamera(0)
print(cam.SerialNumber)

cam.CycleMode = "Fixed"
acqs = cam.acquire_series(circ_buf=True, frame_count=20,
                          max_buf=5, timeout=20000)
print("{} CycleMode Expecting {} images - Returned {} images".format(
                                                cam.CycleMode, 5, len(acqs)))

cam.CycleMode = "Continuous"
acqs = cam.acquire_series(circ_buf=True, frame_count=40,
                          max_buf=7, timeout=20000)
print("{} CycleMode Expecting {} images - Returned {} images".format(
                                                cam.CycleMode, 7, len(acqs)))
