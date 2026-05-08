**TODO:**

- ~~ The viewport right-click is overwriting the right-click for the ROI graphs. Make the viewport right-click only occur when the viewport background is pressed.~~
- ~~ The "Acquire" button needs to become enabled when the preview is stopped and saving is completed.~~
- ~~ In the Preview window, the thumbnails for the ROIs are not taking into account the drift correction which is causing the thumbnail image to be offset from where the ROI is positioned.~~

- ~~ Remove the ROI Scaling section in the "Preview" settings. Combine it with the "ROIs" section.~~
- ~~ In the Preview window, the thumbnail for the ROIs is not being updated on every frame. The y-axis on the graph for the ROI also seems to only be showing a single zero value for all x-axis values. The autoscaling is also setting to +8e37 to -8e37. Figure out why the mean calculation is failing.~~
- [ ] In the preview window remove the "`<n>` ROIs" text.
- [ ] Make the ROI graphs have "Auto Fit" enabled by default.
- [ ] Make the ROI graphs have the `cross` button be on the top-left instead of the top-right
- [ ] Add an "Auto-Fit" button to each ROI (next to the cross button) which instantly re-fits the scaling
- ~~ Remove the ROI Scaling section in the "Preview" settings. Combine it with the "ROIs" section.~~

**Done:**

- ~~ Currently, the camera has this weird issue where every now and then a frame will timeout. In the software, when running a preview or acquisition this results in the error "Error waiting for frame - 13 (AT_ERR_TIMEDOUT)" and then the preview or acquisition stops. Could you make it so that the program ignores up to 5 sequential AT_ERR_TIMEDOUT errors. If they happen it should just continue on to the next frame. If 5 happen in a row then it should print a "Timed out more than 5 times" error in the console and safely stop the preview.~~
- ~~ Make it so that the preview window correctly implements all of the signal processing using the same signal processing pipeline.~~
- ~~ Make it so that the ROI graphs in the preview window are fully calculated whenever settings are changed or the ROI window is changed. It should calculate it once and then remain static.~~
- ~~ Currently when I move or rescale an ROI in the Camera Feed window it starts the ROI's graph from the start. Can you make it so that instead of starting from the start, it sets all of the values before it to null values (so they exist but don't render in the graph) and then append the newest values to the end. The idea being that it should maintain the same x-axis as the other ROI graphs.~~
- ~~ Make it so that the graphs all behave like regular dearpygui graphs. Instead of having the scales be autocalculated manually, have it use the dearpygui calculation. Normally when you right click on an axis in dearpygui it gives you options like "min, max, autoscale". I want it to do this. I also want it to have the behaviours that let you zoom / pan using the mouse. Again, these are all inherent to dearpygui plots so look up in the documentation how to restore this behaviour.~~
- ~~ Add a "Crop" slider into the feed controls. This should crop the processed image as the final processing step before display by setting the pixels outside the crop region to black (as opposed to actually resizing the image). It should also make thexe pixels excempt from "zero-referenced display" calculations.~~
- ~~ Restore the "Zero on Start" functionality. in the Acquisition Settings section of "Camera Controls".~~
- ~~ Add a "Zero on Start" setting to the "Preview Settings" which works the same as the one in the "Acquisition Settings" but only when the preview is started.~~
- ~~ Make the "Acquire" button be the regular button color whilst inactive.~~
- ~~ Make the "Stop Preview" and "Stop Acquiring" buttons red.~~
- ~~ Make the "Acquire" and "Preview" buttons disabled whilst saving~~
- ~~ Make the "Save" button be half the size that it currently is.~~
- ~~ Remove the "Open" button and its functionality~~
- ~~ Remove the "Calculate Frame Mean" checkbox~~
- ~~ Make the buttons in the "Camera Controls" window display over the top of the settings. The buttons area should have a grey background so that they don't overlap weirdly.~~
- ~~ Double check the hardware Reqs calculation. Currently a 60-second, 500x500 pixel, 60FPS video is coming out at 1.68GB is this correct? The image buffer should be 16-bit right, so 16-bits per frame * 60FPS * 60s = 57600 bits = 14,400 Bytes = 14.4 MB. Is this maths correct? If it is then make sure that the hardware reqs is calculating the correct RAM and Hard-Drive space required for the video.~~
- ~~ Add the ability to delete videos in the File Browser. Make sure to add a "Are you sure you want to delete..." modal when the delete button is pressed.~~
- ~~ Make it so that when I drag and drop and NPZ file anywhere into the program it opens it up as in the preview window.~~
- ~~ When loading a file in the "Preview" window, add a loading bar where it currently says "Loading XXX.npz" which displays how much of the file has been loaded.~~
- ~~ Make it so that you can right click anywhere on the background of the program, or on the title-bar of a window and press "Reset All Windows" or "Save Windows State" to save or restore the windows state. Also add a "Collate All Windows" to make all the windows stack themselves side-by-side~~
- ~~ Figure out how to speed up saving. Currently it takes AGES to save a file (understandably its over 2GB file but it should take like 20 seconds at my max write speed of 300MB/s).~~
