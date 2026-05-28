



gem with mpc6000

## User Manual
















Table of Contents




## TABLE OF CONTENTS PAGE | 2
Table of Contents ............................................................................................................................................. 1

Warranty Information ........................................................................................................................................ 4

Introduction ....................................................................................................................................................... 6

Laser Safety ..................................................................................................................................................... 7

System Specifications ...................................................................................................................................... 8

PSU Configuration Drawings ......................................................................................................................... 10

Laser Operation.............................................................................................................................................. 11

Cooling Requirements and Power Consumption .......................................................................................... 17

Power Consumption ....................................................................................................................................... 19

Laser Maintenance ......................................................................................................................................... 19

Liability ............................................................................................................................................................ 19


## CUSTOMER SUPPORT
Before contacting us for assistance, review appropriate sections in the manual that may answer your questions.
After consulting this manual, please contact one of our worldwide offices between 9 AM and 5 PM local time.
Should the laser fall below acceptable specification performance, please contact our service and support team
on +44 161 975 5306 or submit a service request through our website
here. We will provide initial assistance to
rectify the problem remotely. If this is not possible, we will provide you with a Return Material Authorisation
(RMA) Form and instruction on how to package and return the laser safely to us for assessment.
For our commitment to the ‘Waste Electrical’ compliance requirements we recommend you to return your
systems back to the manufacturing site at end of life.
This take-back service will enable us to put the systems beyond use and disseminate the parts into recycling
waste streams.
PLEASE DO NOT RETURN THE LASER WITHOUT PRIOR CONTACT WITH AND AGREEMENT FROM OUR
## SUPPORT TEAM.











## TABLE OF CONTENTS PAGE | 3
Novanta UK
## Unit 1.
## Orion Business Park,
## Bird Hall Lane,
## Stockport,
## SK4 0XG
## UK
## Tel: +44 (0) 161 975 5300

## Novanta Corporation
Sales and Service Support
## 47673 Lakeview Blvd.
## Fremont
## Ca 94538, Usa
## USA
## Tel: +1 510 210 3034



## WARRANTY INFORMATION PAGE | 4

## Warranty Information
The company provides a return to base warranty across all our product ranges. See contact details in the Support
section.
Warranty cover for the laser is subject to proper use, care and protection from mistreatment. Examples of
mistreatment include but are not limited to any of the following:
-     Any deviation from the instructions laid out in the Operating Manual
-     Opening the product or breaking the warranty seals
-     Operation in any hostile environment as outlined in the Operating Manual
-     Any damage due to operation in unclean environments
-     Any substantial mechanical shock
-     Any damage through static discharge (this will not occur under normal operation)

The definition of mistreatment and its applicability to the warranty is at the reasonable discretion of Laser Quantum.
Laser Quantum’s obligation under this warranty is limited to the replacement or repair of the product which having
been returned to the factory is found to be defective, and where the defect was not caused by factors external to the
product. Any replacement part/product is under warranty for the remainder of the initial product warranty period.


## Warning: Serious Personal Injury
Failure to read this manual carefully before operating the laser may result in catastrophic damage to the system which
may void the warranty.

## WARRANTY INFORMATION PAGE | 5
Summary of EU Compliance (SUMEU-GEM-MPC-v1)
## 1. Product
## Product
## Controller
## Wavelength
## Reference
## Gem
## Mpc6000
## 473nm, 532nm, 561nm, 660nm, 671nm
DOCEU-GEM-MPC-v1
- Manufacturer Novanta PHOTONICS, Stockport, UK
- This declaration is issued under the sole responsibility of the manufacturer
- The product described above is in conformity with the relevant Union harmonisation legislation:
2014/35/EU   Low Voltage Directive (LVD)
2014/30/EU   Electromagnetic Compatibility (EMC) Directive
2011/65/EU   Restriction of the use of certain hazardous substances (RoHS)
- References to the relevant harmonised standards used or references to the other technical specifications in relation
to which conformity is declared:

EN 60825-1:2014   Safety of Laser Products

EN 61010-1:2010+A1:2019 Safety requirements for electrical equipment for measurement and
laboratory use
EN 61326-1:2021   Electrical equipment for measurement, control and laboratory use  - EMC
requirements. General requirements for immunity and emissions

- Further information on the technical file or official declaration of conformity is available from the manufacturer at
the address above
Technical File   GEM-TF-v1








## INTRODUCTION PAGE | 6
## Introduction
## The
gem
is a Diode-Pumped Solid-State (DPSS) laser system emitting light in the visible region of the spectrum at
532 nm, 561 nm, 640 nm, 660 nm or 671 nm, depending on the variant ordered. It is a Class 3b or Class 4 laser
product. This manual describes the set up requirements and operational procedures to ensure safe operation of the
system.

## Operational Requirement: Electrical Specification
## The
mpc6000
Power Supply Unit (PSU) requires:
Input Voltage 12 V DC Acceptable range 11 V to 14 V (Ripple 1% peak to peak)
Input Current 8 A Minimum of 8 A must be available from the external source

## Important Note
: The center pin of the input connector is positive, and the external DC source output shall
not be referenced to mains ground. If the system experiences significant power interruption (surges or dips) it will
restart and revert to a safe standby safe mode.

## Operational Requirement: Environment
Optimal Operating Temperature Range – Laser Head       22ºC to 37ºC
Maximum Operating Temperature – Laser Head   40ºC
Maximum Operating Temperature – PSU   50ºC
For optimal performance, the laser head should be mounted onto an appropriate heatsink in a stable environmental
temperature. The heatsink requirements will depend on the ambient temperature of the operating environment and
the operating power of the laser (Cooling Requirements and Power Consumption section).
In the event of the laser head or PSU over-heating, a controlled shut down of the system will occur. The system can
only be restarted once it has been cooled to a safe temperature.
## Storage Requirement: Environment
Temperature Range:      5ºC to 45ºC
Humidity:                        Non-Condensing




## Warning: Serious Personal Injury
Use of controls or procedures or performance of procedures other than those specified herein may result in
hazardous radiation exposure.
Use laser ONLY for the originally intended requirement such as for
scientific research, industrial application or for oem integration.

## LASER SAFETY PAGE | 7
## Laser Safety
## The
gem
is a Class 3b or Class 4 laser product, this is denoted by the laser warning label affixed
to the laser head. A further label also appears adjacent to the laser aperture.





A further label also appears adjacent to the laser
aperture:





When operating the laser, those in the environment must adhere to the following instructions to avoid eye damage
and prevent the risk of fire:
-     Laser safety goggles must be worn at all times when the laser is in operation.
-     Always ensure the beam is safely collected in a suitable beam stop or that the laser is disabled when not in
use.









For a full description of laser safety procedures, the user is referred to Declaration of Conformity standards plus:

- FDA Code of Federal Regulations (CFR) Title 21 Subchapter J section 1040.10 Laser products
- American National Standards for Safe Use of Lasers – ANSI Z136




## SYSTEM SPECIFICATIONS PAGE | 8
## System Specifications
A full list of parts supplied with the laser systems appear below along with the dimensions (mm) and weights (kg).
These measurements should be referred to whilst integrating the system.

## Parts List
## The
gem
laser system comes complete with:

## •
gem
laser head
## •
mpc6000
Microprocessor Controlled Power Supply Unit (PSU)
## •
## Umbilical Cable
– to connect the laser head and PSU

Depending on the purchase specification, some or all of the following items may also be included:

-     RS232 Serial Cable – for communication with the
mpc6000
via computer
-     External Power Supply Source (Mains AC to 12 V DC Desktop Module)
-     IEC Mains Lead
-     PSU Key Set – to operate the interlock key on the PSU control panel
-     Interlock dongle – to enable the laser system (Green spot on casing and Green LED)*

## Important Note
:  If a red spot interlock dongle has been supplied, The Laser Operation Section is amended
to include an additional safety warning which must be taken into consideration before operating the laser.
System parts and accessories that meet manufacturer’s specification MUST only be used. DO NOT replace the IEC
Mains Lead with alternative inadequately rated leads.
















## SYSTEM SPECIFICATIONS PAGE | 9
Weights and Dimensions

gem laser head
Weight: 0.75 kg




















mpc6000 PSU         Weight: 1.7 kg










## PSU CONFIGURATION DRAWINGS PAGE | 10
PSU Configuration Drawings









## Figure 4-1










## Figure 4-2


## Warning: Serious Personal Injury
## The
gem
laser contains components which can be damaged if exposed to an electro-static discharge. Ensure the
connector pins on the back of the laser head are never exposed to an electro-static discharge.


## LASER OPERATION PAGE | 11
## Laser Operation





Switching the laser “on”

## 1
Ensure the
mpc6000
is not powered (i.e. 12 V DC source is switched off).
## 2
Connect the Laser Umbilical Cable to the port marked ‘Laser Umbilical’ on the
mpc6000
before connecting
the other end to the laser head. Tighten the locking posts on the screws at both ends so they are finger tight.
## 3
Switch on the 12 V DC source, this should illuminate the
mpc6000
green power LED. At this stage the
thermal control circuitry is activated but no laser emission should occur.
## 4
The analogue Control port (see Figure 4-2) is multi-functional as it has connections for Interlock, Enable
Switch and Laser Power Control/Modulation. The Interlock must be closed to allow the laser to operate and
this can be achieved using the supplied green-spot Interlock Dongle.
## 5
With the Key Switch turned to the ‘on’ position, a momentary press of the Enable button will start the laser.
## 6
Using the Encoder and Menu Up/Down buttons (see Figure 4-1) the operation current or power can be
adjusted (see Section: Front Panel Controls).
## 7
The laser can be operated in either Power mode or Current mode, the selection of either mode is described
below.

## Power Mode
The laser power is constant, and a feedback control loop maintains the power at the level requested by the operator
via the Front Panel LCD.

Current Mode (where available)
The pump diode current as selected by the operator is a percentage of the total pump diode current available. The
requested current remains constant, however the power may drift due to environmental changes.

Switching the laser “off”
The laser is switched off by turning the Key Switch to the ‘off’ position or disabling the interlock connection. The gem system
MUST NOT be positioned so that it is difficult to operate the disconnecting devices.




## Warning: Serious Personal Injury
If a red spot Interlock Dongle has been supplied with the laser system this will over-ride the need to press the
Enable button. Turning the Key Switch to the ‘on’ position will start the laser


## LASER OPERATION PAGE | 12

## Front Panel Controls
On the front panel of the PSU:

‘Up’ button is marked         ‘Down’ button is marked

In order to adjust the laser output, the user must first select the parameter mode displayed on the LCD screen by
pressing the menu down button. This action changes the text to the navigation colour blue. Depressing the menu
down button again will change the text to the selected colour red. The rotary encoder allows the selection of the
required parameter (power or current). Once selected, depressing the menu up button once will return the parameter
back to the navigation colour blue. The laser is now operating in the selected mode.
Using the rotary encoder select the parameter power or current, depending on selected mode. Depressing the menu
down again will change the selected colour red. The value can then be changed using the rotary encoder. The value
will change faster if the rotary encoder is depressed whilst rotated.

Once the desired value is reached, depressing the menu up button twice will store the parameter in long-
term memory.
The selected parameter power or current - depending upon the operating mode – is represented on the top
horizontal bar. The actual output power is displayed on the screen and on the bottom horizontal bar.
Both the laser head and power supply temperatures are displayed on the screen, as well as the Status display. The
Status messages are tabulated below:

## Status Message Description
## KEYSWITCH :OFF
Keyswitch/Interlock disabled
ENABLE TO START Laser ready, awaiting Enable button
LASER DISABLED Laser disabled via RS232 command
LASER EMISSION Danger! Laser emission





## LASER OPERATION PAGE | 13

## Control Port - Functionality
## The
## MPC600
can be operated directly via the control port (see Figure 4.2) by applying 0 to 5 V, interlocks and push
button in accordance with the diagrams in this section.
9-way Function Table and Pin-Out Diagram





## Pin Function
1 +5 V rail (output)
2 Ground (GND)
3 Enable switch (connection 1)
4 +5V (input)/Diode current enable (connect to pin 1)
5  Interlock (connection 2) and Enable LED anode & Ground (GND)
## 6 Interlock (connection 1)
7 Enable LED cathode
8 Enable switch (connection 2)
9 Ground (GND)
The remote Interlock, Enable and Enable LED can be wired in accordance with the diagrams below [Fig 5.1 & Fig
5.2], to be used as part of a laboratory interlock safety circuit. If either Interlock is broken the system will shut down,
the Enable button must be pressed to restart the system.
Connections to the control port shall be “potential free” i.e. isolated from mains voltage by a barrier rated at 3 KV
## (e.g. Double Insulation, Etc).
The combined electrical resistance of the “Enable Switch”, “Interlock” and associated wiring shall be less than 5
ohms.
If a solid-state device such as on opto-isolator is used as the switching element(s), the total combined voltage drop
must not exceed 500 mV.
The current through the “Interlock” and “Enable Switch” circuitry is less than 100 mA. The LED current source is 10 V
via a 540 R resistor.







Figure 5-1                                Figure 5-2                                     Figure 5-3

## LASER OPERATION PAGE | 14
The PSU has its own 5 V source (pin 1) which must be shorted to pin 4 to allow full Power Mode operation. By wiring
a variable potentiometer in accordance with the diagram [Fig 5-3] the laser current can be varied smoothly (Current
Mode only).

It is recommended that an LED is always connected between pin 5 and 7, to show when the laser is active.
The minimum connections that need to be made for the system to operate are:
-     Pin 6 to Pin 5
-     Pin 3 to Pin 8
-     Pin 1 to Pin 4

RS232 Port – Functionality
Control of the laser can be achieved via the RS232 port using a terminal emulator such as HyperTerminal or PUTTY.
This allows the operator to:

## •
Turn the laser on/off
## •
Control the laser power
## •
Prompt the processor for information such as laser head/PSU temperature
## •
Check the laser status

It is necessary to have the Interlock and Enable switches closed via the Control port in order to enable the laser, prior
to controlling the laser through the RS232 port. Pins 1 and 4 of the Control port must be shorted together to allow
maximum current to be set by the RS232 commands.

The RS232 port uses the standard 9-way connector pin configuration:
Pin 2 TXD: RS232 – Transmit
Pin 3 RXD: RS232 – Receive
Pin 5 GND – Ground

Port settings are:
## Baud Rate: 19,200
## Parity: None
## Stop Bit: 1
## Hand Shaking: None

The operator must wait for a response from the PSU before sending the next command. A response is any text string
(including null) followed by a carriage return, Line Feed.


## LASER OPERATION PAGE | 15
The system has been tested for compliance using a 3 m long serial cable. If a serial cable of over 3 meters in length
is used compliance of the system may be compromised. Therefore, it is recommended that if this is required, optical
isolation should be used.
Note that most PCs do not have an RS232/Serial port as standard so a USB to Serial (RS232) adapter (sold
separately) is needed that is able to go to the full RS232 voltage levels for the connection to function correctly. For
recommendations on adapters or more information please contact your sales representative.

RS232 Port – Serial Commands

## Serial Command
## Function
OFF Disables the laser, regardless of the interlock status
ON Enables the laser subject to Interlock and Enable Switch status
CONTROL=CURRENT Sets the Current mode on
CONTROL=POWER Sets the Power mode on
## CURRENT=###
This sets the current to the diodes as a % of the maximum e.g. to
set a current of 85% of maximum send CURRENT=85, followed by
striking the RETURN key.*
## POWER=###
This sets the output power of the laser. For example, to set a power
of 2800 mW, send the string POWER=2800, followed by striking the
RETURN key.
POWER? Returns the power of the laser (read from the internal photodiode)
## STEN=YES / NO
Enable (YES) or disables (NO) laser as default at start-up. This
serial command must be followed by WRITE
## STPOW=###
### is the optical power in mW. Sets the default start-up power. This
serial command must be followed by WRITE
ACTP=### ### is in mW. Recalibrates the APC mode (See section 5)
WRITE Stores APC calibration, STEN and STPOW in memory
LASTEMP? Returns the temperature of the laser head in degrees centigrade
PSUTEMP? Returns the temperature of the PSU in degrees centigrade
STATUS? Returns the status of the Interlock
## TIMERS?
Returns the timers of the laser and PSU:
Time=#######.# Total time the system has been powered
Laser Time=#######.# Total time the diodes have been powered
Laser > 1A Time=#######.# Total time the diodes have been
powered >1 A
VERSION? Returns the timers of the laser and PSU:


*A minimum % current threshold level is required to achieve laser emission. This threshold varies from laser to laser
and is also dependent on the laser power.
The system has been tested for compliance using a 3m serial cable. If a serial cable >3m is used, compliance of the
system may be compromised unless optical isolation is used.


## LASER OPERATION PAGE | 16

RemoteApp
Using our unique RemoteApp

software suite, the laser can be controlled via the RS232 port. It can be downloaded
from www.novantaphotonics.com. Follow the on-screen prompts to install the software onto the computer.
RemoteApp

includes a comprehensive instruction manual which can be accessed via the ‘Help’ and ‘Contents’ tabs.
The RemoteApp

can also be used if a remote connection is required by our Service & Support Centre and is a
powerful tool if performance optimisation is required.

Re-calibrating the laser power
The laser can be recalibrated at any time during its use to ensure the APC mode is in good agreement with any
external power meter device. Recalibration is a simple process that takes place via the RS232 port. The procedure
for recalibration requires the RS232 port to be configured for use with a terminal emulator (Section 5). The following
procedure must be followed:

## 1
Select an intermediate power that the laser is capable of reaching. Set the laser to this power by typing the
command POWER=###. “###” represents this intermediate or characteristic operating power level in mW.
## 2
After a period of 5 minutes, measure the actual power using a trusted, external power meter.
## 3
Type the command ACTP= [external power meter reading in mW].
## 4
Confirm that the laser has adjusted its power such that the external power meter now reads ###mW within a
few mW.
## 5
If necessary, repeat steps 3 and 4 until the calibration agrees.
## 6
Once accepted, type the command WRITE to store the new calibration.


## Important Note
: The power is calibrated during manufacture and may be subject to an error of up to 5% as
a result of power meter variation.



## Important Note
: Take extra care to remove back-reflections to the laser.  Any magnitude of back reflection
may disturb the resonant cavity and will affect the APC feedback control loop.



## COOLING REQUIREMENTS AND POWER CONSUMPTION PAGE | 17
Cooling Requirements and Power Consumption
## Cooling Requirements
The laser has a characteristic warm-up period before it reaches specification; this time depends partly on the
heatsink to which it is attached. However, the typical warm-up time is 10 minutes from switch on.

For the laser to perform to specification it must be adequately heatsinked to the base of the laser.  Operating the
laser on an inadequate heatsink will adversely affect its stability and may result in thermal shutdown of the laser or
reduction in optical power.
In normal laboratory conditions, a heatsink with dimensions 400 x 400 x 10 mm should
be suitable for the laser to operate in accordance with its specifications. Depending upon environmental conditions
and power of the laser, additional cooling aid might be required (e.g. TEC, forced air cooling, water cooling).
Examples of heatsinking solutions are shown below. For further information on heatsinking your laser system, please
consult your sales representative who may be able to provide a heatsink solution.


Water cooled heat sink.

Air cooled heat sink




## COOLING REQUIREMENTS AND POWER CONSUMPTION PAGE | 18
gem laser head

Once the maximum operational temperature for the laser head (see Section 1.2) has been reached, one of two
things will occur: To ensure the correct cooling arrangement the flow direction should be as follows:
## 1
The current to the diode will immediately be switched off and the laser system will need to be restarted once
the temperature has been restored to normal
## 2
If de-rating is enabled the current will gradually be reduced to zero in order to try to allow the laser head
temperature to stabilize. If the current does reach zero the system will need to be restarted. The effects of
de-rating are shown in the table below:

Current Laser Head/Laser OVERTEMP LED
Colour of  Laser and PSU text
on LCD
A set by user Off Orange
Begins to de-rate Flashes Flash Red and White
Reduced to zero On Flash Red and White

mpc6000 PSU
A similar arrangement occurs for the PSU once the maximum operating temperature (see Section 1) has been
reached. Note: The indicator LED in this instance is marked ‘PSU OVERTEMP LED’.


## POWER CONSUMPTION PAGE | 19
## Power Consumption
The power consumption shown is that which is drawn at the plug from the mains supply in both the Maximum and
Typical states. The Maximum power will usually be drawn at start up and the typical power is when all temperatures
are stabilized, and the system is operating at the specified power. Peak values are shown in all cases and
efficiencies will vary between systems of different wavelengths.
Maximum at 240 V supply – using the supplied External Power Supply Source:
## 120 W
At the 12 V input the MPC6000 can draw maximum of 8 A

## Laser System:
Wavelength (nm) Laser System Output (mW)
## 532 50 250 750 2000
## 640 500 - -  -
## 660 50 125 375 1000
## 671 50 80 250 750
Power Consumption (W)
(Maximum)
## 43 45 60 75
Power Consumption (W)
(Typical)
## 33 35 45 55
The power dissipation of the laser head is no more than 40% of the total power consumption. The values shown are
system power consumption.
## Laser Maintenance
If the
gem
is operated in a smoky or dirty environment, occasional cleaning of the laser window may be necessary.
To perform this procedure, the laser must be turned off and, using optical cloth dampened with research grade
methanol, the laser window must be gently wiped.

-     Always follow the instructions given in this Operating Manual
-     Never touch the connector on the laser head with anything other than the Umbilical cable provided and
always follow the connection instructions in this Operating Manual
-     Do not open the laser head or PSU; this will immediately invalidate the warranty
-     Do not subject the laser head to mechanical shock; if severe this can cause mis-alignment of the laser cavity
-     Do not allow the output window of the laser to be touched as this may damage the precision optical coatings
used. Avoid very dirty atmospheres where dirt may settle on the window
-     Do not operate or store this laser system in very humid or damp environments


## Liability
Novanta accepts no liability for damage to persons or property caused by incorrect or unsafe use of any of its
products; this is the sole responsibility of the user. Proper safety regulations for the use of these products must be
observed at all times.

## LIABILITY PAGE | 1
















This page is intentionally left blank




## Novanta

## Manchester, United Kingdom
## Unit 1, Orion Business Park, Bird Hall Lane.
Stockport, SK4 0XG, UK

## Email:
photonics@novanta.com
Website: www.novantaphotonics.com

## R-   0151 Revision 3.0
## May 2024
## ©2024   Novanta Corporation.