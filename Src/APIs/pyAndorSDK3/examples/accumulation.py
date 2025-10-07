from pyAndorSDK3 import AndorSDK3

print("Connecting to camera")

sdk3 = AndorSDK3()
cam = sdk3.GetCamera(0)


# example 1 : Fixed CycleMode
print("Setting Fixed CycleMode")
cam.CycleMode = "Fixed"
num_frames = 5
cam.AccumulateCount = 5

# when using accumulate need to set cameras Frame count feature to
# num_frames desired muliplied by the AccumulateCount
cam.FrameCount = num_frames * cam.AccumulateCount
print(cam.FrameCount)

acqs = cam.acquire_series(timeout=10000)
print("Acquisition Complete")
assert len(acqs) == num_frames


# example 2 : Continuous CycleMode
# alternatively can set CycleMode to Continuous
print("Setting Continuous CycleMode")
cam.CycleMode = "Continuous"
num_frames = 3
cam.AccumulateCount = 4

# When using Continuous it is necessary to set the frame_count keyword
# in acquire_series
acqs = cam.acquire_series(frame_count=num_frames)
print("Acquisition Complete")
assert len(acqs) == num_frames


# Note: acquire_series does not support doing accumulations with
# SoftwareTrigger - if this is required please see the callbacks.py
# example 2 "Events" for how to do this using ExposureEndEvent and callbacks
