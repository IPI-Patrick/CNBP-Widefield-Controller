from pyAndorSDK3 import AndorSDK3

print("\nConnecting to camera...")

cam = AndorSDK3().GetCamera(0)

print("Using camera: {}".format(cam.SerialNumber))

# Turn on Metadata
cam.MetadataEnable = True

# Turn IRIG on if implemented in camera
irig_enabled = False
try:
    cam.MetadataIRIG = True
    irig_enabled = True
except AttributeError:
    print('MetaDateIRIG not implemented')

# Acquire an image
acq = cam.acquire()

if cam.MetadataEnable:
    if cam.MetadataFrameInfo:
        print("\n-----------\nFrame Info\n-----------")
        print("Width:\t\t", acq.metadata.width)
        print("Height:\t\t", acq.metadata.height)
        print("Stride:\t\t", acq.metadata.stride)
        print("Pixel Encoding:\t", acq.metadata.pixelencoding)

    if cam.MetadataTimestamp:
        print("\n-----------\nTime Stamp\n-----------")
        print("TimeStamp:\t", acq.metadata.timestamp)

    if irig_enabled:
        print("\n----------\nIRIG Data\n----------")
        print("Nanoseconds:\t", acq.metadata.irig_nanoseconds)
        print("Seconds:\t", acq.metadata.irig_seconds)
        print("Minutes:\t", acq.metadata.irig_minutes)
        print("Hours:\t\t", acq.metadata.irig_hours)
        print("Days:\t\t", acq.metadata.irig_days)
        print("Years:\t\t", acq.metadata.irig_years)

cam.close()
