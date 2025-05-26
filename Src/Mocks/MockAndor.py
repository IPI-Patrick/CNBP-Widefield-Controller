from ctypes import *
from PIL import Image
import sys
import numpy as np
import time
import threading


class Andor:    

    serial              = 1234567890

    frame_width         = 100
    frame_height        = 100

    image_width         = frame_width
    image_height        = frame_height

    mock_data           = (np.random.rand(image_width * image_height) * 65535).astype(np.uint16)
    mock_loop_thread    = None

    driver_event_callback = lambda: print("Driver event callback not set")


    def __init__(self):
        
        cw              = self.frame_width
        ch              = self.frame_height

        self.error        = 20002
        self.width       = cw
        self.height      = ch
        self.temperature = None
        self.set_T       = None
        self.gain        = None
        self.gainRange   = None
        self.status      = ERROR_CODE[self.error]
        self.preampgain  = None
        self.channel     = None
        self.outamp      = None
        self.hsspeed     = None
        self.vsspeed     = None
        self.serial      = None
        self.exposure    = None
        self.accumulate  = None
        self.kinetic     = None
        self.ReadMode    = None
        self.AcquisitionMode = None
        self.scans       = 1
        self.hbin        = 1
        self.vbin        = 1
        self.hstart      = 1
        self.hend        = cw
        self.vstart      = 1
        self.vend        = ch
        self.cooler      = None
        self.event       = None
    
    def print(self, *args, **kwargs):
        return
        # print(*args, **kwargs)

    def Initialize(self):
        self.print("MockAndor: Initialize")
        return ERROR_CODE[self.error]

    def GetCameraSerialNumber(self):
        return self.serial.encode('utf-8')


    def SetReadMode(self, mode):
        #0: Full vertical binning
        #1: multi track
        #2: random track
        #3: single track
        #4: image
        self.ReadMode = mode
        self.print("MockAndor: SetReadMode to", mode)        

        return ERROR_CODE[self.error]

    def SetAcquisitionMode(self, mode):
        #1: Single scan
        #2: Accumulate
        #3: Kinetic scan
        #4: Fast Kinectic
        #5: Run till abort
        self.AcquisitionMode = mode
        self.print("MockAndor: SetAcquisitionMode to", mode)

        return ERROR_CODE[self.error]

    def SetTriggerMode(self, mode):
        #0: Internal
        #1: External
        #6: External Start
        #7: External Exposure
        #9: External FVB
        #10: Software Trigger
        #12: External Charge Shift
        self.TriggerMode = mode
        self.print("MockAndor: SetTriggerMode to", mode)

        return ERROR_CODE[self.error]

    def SetExposure(self, exposure):
        # Set the exposure time
        self.exposure = exposure
        self.print("MockAndor: SetExposure to", exposure)
        return ERROR_CODE[self.error]

    def SetImage(self, binningX, binningY, xStart, xEnd, yStart, yEnd):
        # Set the image size and binning
        self.binningX       = binningX
        self.binningY       = binningY
        self.xStart         = xStart
        self.xEnd           = xEnd
        self.yStart         = yStart
        self.yEnd           = yEnd
        self.image_width    = xEnd - xStart + 1
        self.image_height   = yEnd - yStart + 1

        self.print("MockAndor: SetImage to", binningX, binningY, xStart, xEnd, yStart, yEnd)
        return ERROR_CODE[self.error]

    def setDriverEvent( self, event, callback ):
        # Set the driver event
        self.event = event
        self.print("MockAndor: setDriverEvent to", event)        

        # Make it so that 'callback' is called then the event occurs
        self.driver_event_callback = callback

        return ERROR_CODE[self.error]

    def runDriverEvent( self, event ):
        # Run the driver event
        self.print("MockAndor: runDriverEvent to", event)        

        # Call the callback function
        if self.event == event:
            self.driver_event_callback()

        return ERROR_CODE[self.error]

    def SetSingleScan(self):
        self.SetReadMode(4)
        self.SetAcquisitionMode(1)
        self.SetImage(1,1,1,self.width,1,self.height)

        return ERROR_CODE[self.error]

    def StartAcquisition(self):
        self.print("MockAndor: StartAcquisition")    
        return ERROR_CODE[self.error]    


    running = False
    def runContinuous(self, callback, settings):
        self.SetAcquisitionMode(5)
        self.SetTriggerMode(0)
        self.SetExposure(settings["exposure_time"])
        self.SetImage(1, 1, settings["image_left"], settings["image_left"] + settings["image_width"], settings["image_top"], settings["image_top"] + settings["image_height"])
        self.StartAcquisition()
        
        self.running = True
        while self.AcquisitionMode == 5 and self.running:
            
            # Use numpy's randint for faster random uint16 array generation
            self.mock_data = np.random.randint(0, 65536, size=(self.frame_width * self.frame_height), dtype=np.uint16)
            
            time.sleep(self.exposure if self.exposure else 0.1)
            callback()

        self.setDriverEvent('frameReady', callback)   

        return ERROR_CODE[self.error]

    def runForXFrames(self, callback, num_frames, settings):
        self.SetAcquisitionMode(3)
        self.SetTriggerMode(0)
        self.SetExposure(settings["exposure_time"])
        self.SetImage(1, 1, settings["image_left"], settings["image_left"] + settings["image_width"], settings["image_top"], settings["image_top"] + settings["image_height"])
        self.StartAcquisition()

        self.running = True

        for i in range(num_frames):

            if not self.running:
                self.print("MockAndor: Acquisition stopped")
                break

            # Use numpy's randint for faster random uint16 array generation
            self.mock_data = np.random.randint(0, 65536, size=(self.frame_width * self.frame_height), dtype=np.uint16)
            
            time.sleep(self.exposure if self.exposure else 0.1)
            callback()

        return ERROR_CODE[self.error]
    

    def trigger(self):
        # Trigger the camera
        self.print("MockAndor: trigger")

        # Call the driver event callback
        self.runDriverEvent('frameReady')

        return ERROR_CODE[self.error]

    def AbortAquisition(self):
        self.print("MockAndor: AbortAquisition")

        self.running = False

        return ERROR_CODE[self.error]

    def StopAcquisition(self):
        self.print("MockAndor: StopAcquisition")
        self.AbortAquisition()        

        return ERROR_CODE[self.error]

    def getRamCapacity(self):
        # Return the RAM capacity of the camera. Default is 16GB
        ram_capacity = 16 * 1024 * 1024 * 1024  # 16 GB in bytes
        return ram_capacity
   
    def GetAcquiredData(self, imageArray):

        if (self.ReadMode==4):
            if (self.AcquisitionMode==1 or self.AcquisitionMode==5):
                dim = self.image_width * self.image_height / self.hbin / self.vbin
            elif (self.AcquisitionMode==3):
                dim = self.image_width * self.image_height / self.hbin / self.vbin * self.scans
        elif (self.ReadMode==3 or self.ReadMode==0):
            if (self.AcquisitionMode==1):
                dim = self.image_width
            elif (self.AcquisitionMode==3):
                dim = self.image_width * self.scans

        img_w = self.image_width
        img_h = self.image_height
        
        # imageArray[:] = self.mock_data
        # Create a 2D grid
        y, x = np.ogrid[:img_h, :img_w]

        # Parameters for two blobs
        cx1, cy1 = img_w // 3, img_h // 2
        cx2, cy2 = 2 * img_w // 3, img_h // 2
        r1, r2 = img_w // 6, img_w // 7

        # Time-based sinusoidal brightness
        t = time.time()
        amp1 = 20000 + 15000 * np.sin(t)
        amp2 = 20000 + 15000 * np.sin(t + np.pi / 2)

        # Blob 1
        dist1 = np.sqrt((x - cx1) ** 2 + (y - cy1) ** 2)
        mask1 = dist1 < r1
        blob1 = np.zeros((img_h, img_w), dtype=np.float32)
        blob1[mask1] = amp1 * (1 - dist1[mask1] / r1)

        # Blob 2
        dist2 = np.sqrt((x - cx2) ** 2 + (y - cy2) ** 2)
        mask2 = dist2 < r2
        blob2 = np.zeros((img_h, img_w), dtype=np.float32)
        blob2[mask2] = amp2 * (1 - dist2[mask2] / r2)

        # Combine blobs and add noise
        image = blob1 + blob2
        image += np.random.normal(1000, 500, size=(img_h, img_w))
        image = np.clip(image, 0, 65535).astype(np.uint16)

        # Flatten and copy to imageArray
        flat = image.flatten()
        n = min(int(dim), flat.size, len(imageArray))
        for i in range(n):
            imageArray[i] = int(flat[i])

        self.error = 20002  # DRV_SUCCESS        
        return ERROR_CODE[self.error]


ERROR_CODE = {
    20001: "DRV_ERROR_CODES",
    20002: "DRV_SUCCESS",
    20003: "DRV_VXNOTINSTALLED",
    20006: "DRV_ERROR_FILELOAD",
    20007: "DRV_ERROR_VXD_INIT",
    20010: "DRV_ERROR_PAGELOCK",
    20011: "DRV_ERROR_PAGE_UNLOCK",
    20013: "DRV_ERROR_ACK",
    20017: "DRV_ACQUISITION_ERRORS",
    20024: "DRV_NO_NEW_DATA",
    20026: "DRV_SPOOLERROR",
    20034: "DRV_TEMP_OFF",
    20035: "DRV_TEMP_NOT_STABILIZED",
    20036: "DRV_TEMP_STABILIZED",
    20037: "DRV_TEMP_NOT_REACHED",
    20038: "DRV_TEMP_OUT_RANGE",
    20039: "DRV_TEMP_NOT_SUPPORTED",
    20040: "DRV_TEMP_DRIFT",
    20050: "DRV_COF_NOTLOADED",
    20053: "DRV_FLEXERROR",
    20066: "DRV_P1INVALID",
    20067: "DRV_P2INVALID",
    20068: "DRV_P3INVALID",
    20069: "DRV_P4INVALID",
    20070: "DRV_INIERROR",
    20071: "DRV_COERROR",
    20072: "DRV_ACQUIRING",
    20073: "DRV_IDLE",
    20074: "DRV_TEMPCYCLE",
    20075: "DRV_NOT_INITIALIZED",
    20076: "DRV_P5INVALID",
    20077: "DRV_P6INVALID",
    20083: "P7_INVALID",
    20089: "DRV_USBERROR",
    20091: "DRV_NOT_SUPPORTED",
    20095: "DRV_INVALID_TRIGGER_MODE",
    20099: "DRV_BINNING_ERROR",
    20990: "DRV_NOCAMERA",
    20991: "DRV_NOT_SUPPORTED",
    20992: "DRV_NOT_AVAILABLE"
}