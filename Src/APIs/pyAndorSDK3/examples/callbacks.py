from pyAndorSDK3 import AndorSDK3
from pyAndorSDK3 import ErrorCodes
from pyAndorSDK3 import CameraException

from collections import deque
import numpy as np

print("Connecting to camera")

sdk3 = AndorSDK3()
cam = sdk3.GetCamera(0)
print(cam.SerialNumber)

# Callbacks allow the application to receive notification when a feature has 
# indirect change occur, a callback function can be created and attached to a
# feature. Whenever the feature changes in any way, this callback will be
# triggered, allowing the application to carry out any actions required to
# respond to the change.

# A callback should complete any work required in the minimal amount of time as
# it holds up the thread that caused the callback. If possible the application
# should delegate any work to a separate application thread if the action will
# take a significant amount of time.
# The callback function should not attempt to modify the value of any feature
# as this can cause lockup

@sdk3.event_callback
def callback_func(handle, feature):
    print("callback handle:{}  feature:{}  value:{}  min:{}  max:{}".format(
        handle, feature, getattr(cam, feature),
        getattr(cam, "min_"+feature), getattr(cam, "max_"+feature)))


print("Registering callback on FrameRate:")
# register the feature and callback function and one callback will immediately
# trigger on successful registering
cam.register_feature_callback("FrameRate", callback_func)

print("Updating ExposureTime")
# a second callback will trigger after changing ExposureTime due to the
# dependance of FrameRate on ExposureTime
cam.ExposureTime = cam.min_ExposureTime
cam.unregister_feature_callback("FrameRate", callback_func)


# example 2: Events
@sdk3.event_callback
def event_callback_func(handle, feature):
    print(feature, "triggering")
    cam.SoftwareTrigger()
    print(feature)


def enable_event(event, func):
    try:
        cam.EventSelector = event
    except CameraException as e:
        if e.err_code == ErrorCodes.AT_ERR_NOTWRITABLE:
            raise RuntimeError("Events not available for this camera")
    cam.EventEnable = True
    cam.register_feature_callback(event, func)


# Events:
# EventsMissedEvent BufferOverflowEvent
# ExposureEndEvent ExposureStartEvent
# RowNExposureEndEvent RowNExposureStartEvent
enable_event("ExposureEndEvent", event_callback_func)


print("Setting up Acquisition")
cam.TriggerMode = "Software"
cam.CycleMode = "Continuous"
frame_count = 20
imgsize = cam.ImageSizeBytes
for _ in range(0, frame_count):
    buf = np.empty((imgsize,), dtype='B')
    cam.queue(buf, imgsize)

series = deque()
frame = None
try:
    cam.AcquisitionStart()
    print("Started - triggering")
    cam.SoftwareTrigger()
    frame = 0
    while(True):
        acq = cam.wait_buffer(5000)
        series.append(acq)
        frame += 1
        print(frame)
        percent = int((frame/frame_count)*100)
        print("{}% complete series".format(percent), end="\r")
        if frame >= frame_count:
            print()
            break
except Exception as e:
    if frame is not None:
        print()
        print("Error on frame "+str(frame))
    cam.AcquisitionStop()
    cam.flush()
    raise e
cam.AcquisitionStop()
cam.flush()
