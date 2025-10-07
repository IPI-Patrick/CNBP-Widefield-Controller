from pyAndorSDK3 import AndorSDK3
import os
try:
    from astropy.io import fits
except Exception:
    pass

print("Connecting to camera")

sdk3 = AndorSDK3()
cam = sdk3.GetCamera(0)
print(cam.SerialNumber)

cam.CycleMode = "Fixed"
acqs = cam.acquire_series(frame_count=3, timeout=20000)

# Ensure you have write permission for the destination location
# Creating folder for saved images
if not os.path.exists("save_images_output"):
    os.makedirs("save_images_output")

for i, acq in enumerate(acqs):
    # using acq.save() includes acquisition information
    # in the fits header such as PixelEncoding and AOI settings
    acq.save("save_images_output/acq_{}.fits".format(i), True)

# Manually saving using astropy instead of Acquisiton.save()
for i, acq in enumerate(acqs):
    fits.writeto('save_images_output/manual_acq_{}.fits'.format(i), acq.image)

# For now pyAndorSDK3 only provides saving to fits
# But using acq.image (or acq.raw_data) it is be possible to save to other
# image formats such as tiff, png, etc.
