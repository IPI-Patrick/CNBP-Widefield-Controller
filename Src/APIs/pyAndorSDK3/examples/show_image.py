from pyAndorSDK3 import AndorSDK3

print("Connecting to camera")

sdk3 = AndorSDK3()
cam = sdk3.GetCamera(0)
print(cam.SerialNumber)

cam.CycleMode = "Fixed"
acq = cam.acquire(timeout=20000)

# Ensure you have write permission for the destination location
acq.show()
