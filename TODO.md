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

* Completely refactor the implementation of the image processing. Currently the image processing seems to be split between multiple files: Andor.py and CameraFeed.py, AcquisitionPreviewWindow.py. The way I want it to work is I want Andor.py to have an `Andor.process_frame(frame, settings, rois, storage_location=None)` method and an `Andor.process_frames(frame_buffer, settings, rois, storage_location=None)` method. The `process_frame` method should input a frame and apply all of the processing that needs to be applied to it. Make it so that the `Andor` class holds all of the properties responsible for the image processing settings. The frontend GUI should be updating these properties in the Andor class (things like Min Z, Max Z, Drift Correction enabled, etc...).
* The processing should happen in this order: Drift-Correction, LP-Filter, Difference/Contrast, Coloring, scale-bar, z-scale-bar, text. Currently `Andor.py` has a billion different methods for this. I want everything to be coallesced into a single method with comments separating each individual section. I don't want lots of sub-methods, try to keep it consice, and well commented - I want it to be a single method that handles all of this rendering.
  Andor should have a method `create_processing_thread(settings, rois, raw_frame_buffer, latest_location)` which creates a new thread which only handles the processing of the raw frames in the inputted `raw_frame_buffer` (in the live camera feed, the `frame_buffer` would be `Andor.acquisitions`). The `settings` should be a pointer to a settings dictionary object. The `rois` input should be a pointer to a list of `roi` objects. The thread should continuously run `Andor.process_frames(frame_buffer, settings, rois)` on all the frames in `raw_frame_buffer` that have not already been processed. This should call `Andor.process_frame(frame, settings, rois)` on every frame unprocessed frame, which processes the frame and stores the latest frame in `latest_location `which in the live camera feed will be `Andor.processed_frame`. Anywhere where the latest frame is displayed should pull directly from the `Andor.processed_frame` property to get the latest frame texture.
  ROIs should be handled by the Andor object. Andor should have an `Andor.rois` property which stores a list of `roi` objects with the properties `{x, y, w, h, mask, plot_x, ploy_y}`. During `Andor.process_frame()`, each ROI should be calculated after processing the raw frame. Each ROI should be cropped using the `roi.mask` (which should be a np mask array allow quick access to the ROI's pixels), and then the mean/max/min-max value should be calculated and stored in `roi.plot_y`, and the frame index should be stored in `roi.plot_x`. These should both be deque buffers with a size of   `Andor.settings.max_acquisitions`. Whenever anything in `Andor.settings` is changed, the `roi` plot buffers should be cleared such that all of the `roi.plot_y` values are set to `nan` (so that they appear as invisible on the graph). Whenever any of the the `x,y,w,h` properties of an ROI are changed, the ROI mask should be re-calculated and then the `roi` plot buffers should be cleared as well.
  Inside of `AcquisitionPreviewWindow.py` the acquisition preview is currently using its own methods for rendering the acquisition preview frames - I don't want anything to be written twice in the code. Make it instead create a new thread which runs `Andor.process_frames(preview_frames, preview_settings, preview_rois, preview_storage_buffer)` in the background whenever we ask it to. This will make it use the `Andor.process_frames()` pipeline for rendering the texture so that it uses the exact same methods, just with a different set of settings and ROIs, and store the resulting frame buffer in the `preview_storage_buffer` location provided. The preview feed should then always use the frame texture found in the `preview_storage_buffer` at the currently selected frame index. When any of the `preview_settings` or `preview_rois` are changed, it should first run `Andor.process_frame` on the current frame and store the result in the `preview_storage_buffer` at the current index, and then run `Andor.process_frames` on the entire `preview_frames` buffer. This way the user gets instant feedback when changing things in the menu, and the UI doesn't lag because it happens in the background. Make sure that `andor.process_frames` has some sort of "stop" mechanic which allows the user to stop processing a set of frames using events, this way if we make multiple changes quickly we are not stuck waiting for all of them to recalculate. Add in a progress bar to the bottom-right of the Preview controls which displays the percentage of frames processed.
  In the `performance_overlay`, make it have a new section called "Processing FPS". This should show the FPS of the processing pipeline in the main `processing_thread`. Make sure it shows the real number of frames per second, rather than just the loops per second of the processing thread (remember it processes frames in batches).
* Refactor this code for AGENT readability. This code is getting extremely large and complex. Agents only have a short context window and that is making handling this codebase very difficult. Many agents in the past have created shitty fixes for code by expanding the codebase by huge amounts by creating unnessecary helper methods. There are likely a lot of duplicate pieces of code, or pieces of code that could easily be combined. There is likely many places where files can be split for readability. I want you to trim as much of the fat off this program as possible, and refactor it so that it is as easy as possible for future AGENTS to understand and make changes to.
* Read through the entire program and fully update the AGENTS.md file to reflect how the program currently works.
* Add in all of the additional controls found in Andor Solis [Shutter Mode (global / rolling / etc...), spurious noise filter, overlap readout, readout rate (500MHz, etc...)  ]. Look through the documentation in `src/APIs` to discover what should be added, and how to implement it.
