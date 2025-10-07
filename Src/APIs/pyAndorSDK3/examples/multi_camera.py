from pyAndorSDK3 import AndorSDK3
from threading import Thread

print("Connecting to camera")

sdk3 = AndorSDK3()
cams = []
for index in range(sdk3.DeviceCount):
    cams.append(sdk3.GetCamera(index))

# cams is now a list of opened cameras on the system
for i, cam in enumerate(cams):
    print("Camera index {} - Serial Number: {}".format(i, cam.SerialNumber))


# each camera cam run independantly in seperate threads
def setup_and_acquire(cam):
    cam.AOIHeight = 1000
    cam.AOIWidth = 1000
    cam.acquire()
    print("{} done acquiring".format(cam.SerialNumber))


threads = []
for cam in cams:
    thread = Thread(target=setup_and_acquire, args=(cam))
    thread.start()
    threads.append(thread)

for thread in threads:
    thread.join()
