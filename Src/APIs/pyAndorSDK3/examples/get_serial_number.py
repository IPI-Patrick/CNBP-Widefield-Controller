from pyAndorSDK3 import AndorSDK3

print("Connecting to camera")

sdk3 = AndorSDK3()
cam = sdk3.GetCamera(0)
print(cam.SerialNumber)
