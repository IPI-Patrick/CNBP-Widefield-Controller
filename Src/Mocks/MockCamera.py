import time
import numpy as np

# Return a mock acquisition object
class Acquisition:

    def __init__(self, BitDepth, AOIWidth, AOIHeight):
        self.image      = np.random.randint(0, 2**BitDepth, (AOIHeight, AOIWidth), dtype=f'u{BitDepth//8}')
        self._np_data   = self.image.flatten().astype('B')
        self.BitDepth   = BitDepth

class MockCamera:
    ExposureTime            = 0.01
    TriggerMode             = "Internal"
    CycleMode               = "Continuous"
    AOIWidth                = 1024
    AOIHeight               = 1024
    BitDepth                = 16
    options_bitDepth        = [8, 10, 12, 14, 16]
    options_CycleMode       = ["Continuous", "Single"]
    options_TriggerMode     = ["Internal", "Software", "External"]
    options_AOIBinning      = ["1x1", "2x2", "4x4"]
    AOIBinning              = "1x1"
    AOILeft                 = 1
    AOITop                  = 1
    SoftwareTrigger         = lambda self: ()
    AcquisitionStart        = lambda self: print("Mocking Starting Acquisition")
    AcquisitionStop         = lambda self: print("Mocking Stopping Acquisition")
    queue                   = lambda self, buf, size: ()
    flush                   = lambda self: print("Mocking Flushing buffers")

    @property
    def ImageSizeBytes(self):
        return self.AOIWidth * self.AOIHeight * (self.BitDepth // 8)

    def wait_buffer(self, timeout):

        # Wait for the exposure time (in seconds)
        time_to_wait = self.ExposureTime        
        time.sleep(time_to_wait)

        return Acquisition(self.BitDepth, self.AOIWidth, self.AOIHeight)
