import math
import time


class RigolDG4162:

	output_state 		= True
	current_voltage 	= 0.0
	current_waveform 	= "SIN"
	current_frequency 	= 0.0
	current_amplitude 	= 0.0
	current_offset 		= 0.0
	current_phase 		= 0.0	


	def __init__(self, resourceManager, address):
		print("RigolMock initialized")



	def write(self, command):
		'''
		Write a command to the VISA resource
		'''
		# print(f"RigolMock write: {command}")



	def query(self, command, returns):
		'''
		Query a command to the VISA resource
		'''
		# print(f"RigolMock query: {command}, returns: {returns}")
		return returns
	


	def close(self):
		'''
		Close the VISA session
		'''
		print("RigolMock closed")


		
	def updateVoltage(self):
		now = time.time()

		if self.output_state == False:
			self.current_voltage = 0.0
			return

		if self.current_waveform == "DC":
			self.current_voltage = self.current_offset

		elif self.current_waveform == "SIN":
			self.current_voltage = (
				self.current_amplitude * math.sin(2 * math.pi * self.current_frequency * now + math.radians(self.current_phase)) + self.current_offset )
			
		elif self.current_waveform == "SQU":
			period = 1.0 / self.current_frequency
			if now % period < period / 2:
				self.current_voltage = self.current_amplitude / 2 + self.current_offset
			else:
				self.current_voltage = -self.current_amplitude / 2 + self.current_offset
		else:
			self.current_voltage = 0.0


	def setSineWave(self, channel, freq, ampl = 0.9, offset = 0, phase = 0):
		command = ':SOURCE'+str(channel)+':APPL:SIN '+str(freq)+','+str(ampl)+','+str(offset)+','+str(phase)

		self.current_waveform 		= "SIN"
		self.current_frequency 		= freq
		self.current_amplitude 		= ampl
		self.current_offset 		= offset
		self.current_phase 			= phase
		
		self.write(command)
	


	def setDC(self, channel, voltage):
		"""
		Set the output 'channel' to a DC voltage.
		"""
		command = f':SOURCE{channel}:APPL:DC {voltage}'

		self.current_waveform 		= "DC"
		self.current_frequency 		= 0.0
		self.current_amplitude 		= 0.0
		self.current_offset 		= voltage
		self.current_phase 			= 0.0

		self.write(command)


	def setSquareWave(self, channel, highV = 1, lowV = -1, period = 1e-3, delay = 0):
		'''
		Set a square wave with arbitrary high, low values
		'''		
		freq		= 1.0/period
		ampl		= (highV - lowV)/1.0
		offset 		= (highV + lowV)/2.0
		phase 		= (delay/period)*360.0

		self.current_waveform 		= "SQU"
		self.current_frequency 		= freq
		self.current_amplitude 		= ampl
		self.current_offset 		= offset
		self.current_phase 			= phase

		command = ':SOURCE'+str(channel)+':APPL:SQU '+str(freq)+','+str(ampl)+','+str(offset)+','+str(phase)
		self.write(command)



	def getVoltage(self, channel):
		"""
		Get the current output voltage of the specified channel.
		"""
		command 	= f':SOURCE{channel}:VOLT?'
		voltage 	= self.query(command, self.current_voltage)
		
		try:
			return float(voltage)
		except ValueError:
			return voltage


	def getOutputState(self, channel):
		"""
		Get the output state of the specified channel.
		"""
		command 	= f':OUTP{channel}:STAT?'
		state 		= self.query(command, self.output_state)
		
		try:
			return int(state)
		except ValueError:
			return state
		

	def setOutputState(self, channel, state):
		"""
		Set the output state of the specified channel.
		"""		
		command 			 = f':OUTP{channel}:STAT { "ON" if state else "OFF" }'
		self.output_state  = state

		self.write(command)