* Make it so that the exposure input in the top-right actually sets the exposure
* Make it so that the exposure inputs only apply when you press enter
* The camera often rejects an exposure / frame-rate combo with an "AT_ERR_OUTOFRANGE" error. If this happens then it should resolve gracefully and lower the frame rate until it is within range. Search the internet to see if there is any good way to calculate or read the maximum frame rate for a given exposure time for the Andor Zyla Camera
* Add a "Temperature" symbol to the right of the exposure slider in the sidebar. This should be red when the camera is not at -9.5 degrees or lower. If it is red and you press it it should turn on the cooler and set it to -10 degrees.
* Make the camera set the cooler to "ON" and "-10 degrees"" when the camera is connected.
* Make the position map not scrollable. It should always show the current x/y/z at the bottom.
* Using the render pipeline, make the Laser Enable/Disable button in the toolbar always reflect the current laser state. It should be green when laser is enabled and regular when laser is disabled. Ensure the laser power in the toolbar also reflects the actual laser power set point.
* Make the laser power in the tool-bar go up to 500mW
* Add a tiny progress bar underneat the laser power slider which shows the current laser power. The height of the progress bar + gap + slider should be the same as the slider before the change.
* Add a "Low Power" button in the toolbar and laser menu which quickly sets the laser power to 1mW.
* Make all inputs only update when enter is pressed
* Make it so that when auto-scale is active it updates the values stored in the Min Z and Max Z settings.
* Add a checkbox that lets me switch between Min Z and Max Z in percent or in absolute values (between 0 and 65535)
* Make it so that the stage controls are disabled whilst typing in any text box.
* Make it so that when exporting a frame as a PNG it defaults the name to the name of the file. Make it so the default save location is the same location as the file as well.
* Add "Title Text" to the list of "Rendered Info" that can be added to the video / picture. Make this be a checkbox and a text input. It should default to the name of the file.
* Make it so that if I drag a .npz file anywhere into the program it opens it.
* Make it so that when it saves a snapshot or video (.npz) that it also saves the current Min Z and Max Z. When that file is opened up it should load that min Z and max Z by default
* Add a "Stage" section to the toolbar. Add a "Lower" button which quickly lowers the Z-axis to 2mm at high speed (3mm/s).
* Move the objective name up a little bit higher so it's closer to the line of the scale bar
* Attempt to make the laser auto-connect when the program starts up. It should go through all of the COM ports, connecting to each until one responds as expected.
* Drift correction is no longer being applied before calculating the difference / contrast. Ensure drift correction is applied before these.
* Add an "Include ROIs in export" button. This should render each ROI as a graph beside the image frame in the exported video / image. The graphs should render as complete graphs with a red line indicating the current frame.
* Every now and then when I start the capture I get the error:
  ```
  Exception in thread Thread-27 (_capture_loop):
  Traceback (most recent call last):
    File "C:\Coding-Projects\Widefield-Controller\.venv\Lib\site-packages\pyAndorSDK3\andor_camera.py", line 390, in queue
      self._lib.queue_buffer(
    File "C:\Coding-Projects\Widefield-Controller\.venv\Lib\site-packages\pyAndorSDK3\andor_sdk3_internals.py", line 337, in queue_buffer
      self.handle_return(ret)
    File "C:\Coding-Projects\Widefield-Controller\.venv\Lib\site-packages\pyAndorSDK3\andor_sdk3_internals.py", line 86, in handle_return
      raise ATCoreException(ret_value)
  pyAndorSDK3.andor_sdk3_exceptions.ATCoreException: 15 (AT_ERR_INVALIDSIZE)

  During handling of the above exception, another exception occurred:

  Traceback (most recent call last):
    File "C:\Users\Admin\AppData\Local\Python\pythoncore-3.12-64\Lib\threading.py", line 1075, in _bootstrap_inner
      self.run()
    File "C:\Users\Admin\AppData\Local\Python\pythoncore-3.12-64\Lib\threading.py", line 1012, in run
      self._target(*self._args, **self._kwargs)
    File "C:\Coding-Projects\Widefield-Controller\src\Drivers\Andor.py", line 602, in _capture_loop
      _queue_capture_buffers()
    File "C:\Coding-Projects\Widefield-Controller\src\Drivers\Andor.py", line 590, in _queue_capture_buffers
      cam.queue(buf, imgsize)
    File "C:\Coding-Projects\Widefield-Controller\.venv\Lib\site-packages\pyAndorSDK3\andor_camera.py", line 393, in queue
      raise CameraException(e.err_code, "Error queuing buffer")
  pyAndorSDK3.andor_sdk3_exceptions.CameraException: Error queuing buffer - 15 (AT_ERR_INVALIDSIZE)
  Capture already running
  ```
