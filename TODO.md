**TODO:**

- ~~My microscopes stage is now motorised - utilising KST101 stepper motor drivers to drive three stepper motors (X, Y, Y). Create a new Driver called `KST101` and implement the python library to make a driver that lets me connect to the motors, jog them, set their positions, get their position, get and set their speed/acceleration/other-settings, and anything else that should be included for good control over the motors. The control of these motors should be handled in a seperate thread so as not to slow down the main thread.~~

  ~~Make a mock stepper motor (in src/Mocks) which responds to the program the same way that the stepper motor would so that I can develop the software without having the hardware available. Make it so that this is only loaded then the python script is run with the "-dev" argument like: "python ./src/WidefieldController.py -dev".~~

  ~~After this, create a new window (not a sub-window) for the motors. It should have a connect / disconnect button like what's seen in `LaserControls.py`. Connecting should identify and connect to all three of the X/Y/Z motors at once. There should be a "KeyPad" section which should have a keypad (6 keys in order top-left to bottom-right: [-Z, +Y, +Z, -X, -Y, +X]) which move the motor smoothly when pressed and immediately stop moving it when unpressed. Above this there should be a graph. This graph should be a square (1:1 ratio) and have just the X/Y axis fixed in the middle). On the graph should be a red square which represents the current location of the stage. To the right of that graph should be another graph (or maybe vertical progress bar if that's possible) this bar should represent the current Z-position.~~

  ~~There should be an "X-Settings" & "Y-Settings" & "Z-Settings" section where you can set the speed, acceleration, backlash, and other motor settings.~~


- ~~Make it so that the X / Y / Z status indicators are shown just to the right of the connect / disconnect button.~~
- ~~Make it so that when the driver is connected it reads in its settings (which are stored on the physical device) and updates the UI. Make it so that the mock device stores the settings in a json to mock this behaviour.~~
- ~~Remove the axis labels and ticks from the Position map. Remove the X/Y/Z range inputs and instead make it be fixed to the maximum travel range of the stepper motor (25mm). Underneath the Position map make there be a horizontal section with X/Y/Z values in millimeters. Also make all of the units in millimeters instead of steps.~~
- Make it so that we can move the stage using the W,A,S,D,Q and E keys on the keyboard.
- 

- [ ] Add auto-focus using KST101
- [ ] Make it so that the MockCamera only activates when the -dev argument is present when running the python script in the same way as the mock KST101 driver.
- [ ] Add shortcuts - space = start/stop preview. Shift+space = start acquisition
- [ ] Make saving faster. Currently it's way too slow
- [ ] Add ability to save video as an MP4
- [ ] Make the Function generator frequency be "Period" instea
