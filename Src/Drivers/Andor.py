
import time
import numpy as np
import threading
from pyAndorSDK3 import AndorSDK3
from collections import deque
from Mocks.MockCamera import MockCamera 

class Andor:

    max_acquisitions            = 200    
    acquisitions                = deque(maxlen=max_acquisitions)    
    timestamps                  = deque(maxlen=max_acquisitions)
    frameIdx                    = 0

    max_mean_buffer             = 1000
    meanBuffer                  = deque(maxlen=max_mean_buffer)

    def __init__(self):

        # Thread-safe data sharing
        self.latest_frame       = None
        self.frame_lock         = threading.Lock()
        self.capture_thread     = None
        self.is_capturing       = False
        self.frame_ready_event  = threading.Event()        
        self.stop_capture_event = threading.Event()

        # Set up the camera
        self.sdk3               = AndorSDK3()

        try:
            self.camera         = self.sdk3.GetCamera(0)
        except Exception as e:
            
            # Add the mock flag to show that no camera was found
            self.isMock = True

            # For development without a camera
            self.camera     = MockCamera()

            print("Error: No camera found. Running in development mode.")
        
        self.latest_frame       = np.zeros((self.camera.AOIHeight, self.camera.AOIWidth), dtype=f'u{self.camera.BitDepth//8}')
    

    def _capture_loop(self, continuous=False, callback=None):    
        print(f"{'Continuous' if continuous else ''} Capture Started")

        # Set up the camera for acquisition
        cam                         = self.camera
        timeout                     = 1000
        imgsize                     = cam.ImageSizeBytes
        soft_trigger                = cam.TriggerMode == "Software"
        cam.CycleMode               = "Continuous"
        buffer_count                = 10

        # Reset the acquisition buffers
        self.acquisitions           = deque(maxlen=self.max_acquisitions)        
        self.timestamps             = deque(maxlen=self.max_acquisitions)
        self.meanBuffer             = deque(maxlen=self.max_mean_buffer)

        # Update the shape of the latest frame
        self.latest_frame           = np.zeros((cam.AOIHeight, cam.AOIWidth), dtype=f'u{cam.BitDepth//8}')

        # Pre-allocate the buffers
        for _ in range(0, buffer_count):
            buf = np.empty((imgsize,), dtype='B')
            cam.queue(buf, imgsize)

        try:
            cam.AcquisitionStart()
            while(True):
                
                # If using software trigger, trigger it
                if soft_trigger:
                    cam.SoftwareTrigger()

                # Wait until the next frame is ready in the buffer
                acq = cam.wait_buffer(timeout)


                # Update the latest frame in a thread-safe manner
                with self.frame_lock:
                    self.acquisitions.append(acq.image)
                    self.timestamps.append(time.time())
                    self.latest_frame = self.acquisitions[-1]
                    self.meanBuffer.append(np.mean(self.latest_frame))
                    self.frame_ready_event.set()
                    self.frameIdx += 1

                    if not continuous and self.frameIdx >= self.max_acquisitions:
                        break

                # Re-add this buffer to the queue
                cam.queue(acq._np_data, imgsize)

                # If the stop event is triggered, stop
                if(self.stop_capture_event.is_set()):                        
                    break

        except Exception as e:
            print("Error occurred during acquisition")
            print(e)
            print()

        # Stop the acquisition
        with self.frame_lock:
            self.is_capturing = False
            self.stop_capture_event.clear()

        print("Preview capture stopped")
        cam.AcquisitionStop()
        cam.flush()

        # Call the callback if provided
        if callback:
            callback(self.acquisitions)

        return


    def start_capture_continuous(self, callback=None):
        self.start_capture(continuous=True, callback=callback)

    def start_capture(self, continuous=False, callback=None):
        
        # Start continuously capturing in a seperate thread
        if self.is_capturing:
            print("Capture already running")
            return
            
        self.is_capturing   = True
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True, kwargs=dict(continuous=continuous, callback=callback))
        self.capture_thread.start()

    def stop_capture(self):
        # Stop the capture thread
        self.stop_capture_event.set()
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2.0)
        self.capture_thread = None

