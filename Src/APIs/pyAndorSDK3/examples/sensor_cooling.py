from pyAndorSDK3 import AndorSDK3
import time

print("Connecting to camera")

sdk3 = AndorSDK3()
cam = sdk3.GetCamera(0)

cam.SensorCooling = True

# getting legal available options for TemperatureControl feature
temps = cam.available_options_TemperatureControl
target_temp = temps[0] if cam.TemperatureControl != temps[0] else temps[1]
print("Target temperature = {}C".format(target_temp))
cam.TemperatureControl = target_temp

# waiting for temperature to stabilise
while(cam.TemperatureStatus != "Stabilised"):
    time.sleep(5)
    print("Temperature: {:.5f}C".format(cam.SensorTemperature), end="  ")
    print("Status: '{}'".format(cam.TemperatureStatus))
    if cam.TemperatureStatus == "Fault":
        err_str = "Camera faulted when cooling to target temperature"
        raise RuntimeError(err_str)

print("Sensor Temperature now Stabilised and Camera is ready to use")
