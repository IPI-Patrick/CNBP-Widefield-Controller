import serial
import time
import threading
import serial.tools.list_ports as list_ports
from Drivers import Andor
from Windows.SubWindows.GraphWindow import GraphWindow
import random

class pHSensor():    

    def __init__(self, Andor:Andor.Andor=None):
        self.interval   = 1        
        self.stop_event = threading.Event()
        self.lock       = threading.Lock()
        self.Andor      = Andor
        self.buffer     = []
        self.timestamps = []
        self.frameNums  = []

        self.mean_graph   = GraphWindow(
            name         = "pH Over Time",
            id           = "pHGraph",
            getYValues   = lambda: self.buffer,
            getXValues   = lambda: self.frameNums,
            xlabel       = "Time (s)",
            ylabel       = "pH Value",
            xpos        = 1015,
            ypos        = 590
        )


    def connect(self, port, baudrate=19200, timeout=1):
        if(self.ser):
            self.ser.close()
            self.ser = None

        self.ser = serial.Serial(port, baudrate, timeout=timeout)

    def read(self):
        
        if not hasattr(self, 'pH'):
            self.pH = 7.00
        
        self.pH += (0.05 - 0.1 * random.random())
        self.pH = max(0, min(14, self.pH))

        # TODO: Implement actual reading from the pH sensor

        # self.ser.write(b'R\r\n')
        # time.sleep(0.1)
        # line = self.ser.readline().decode('utf-8').strip()
        
        # try:
        #     pH_value = float(line)
        # except ValueError:
        #     pH_value = None
        
        return self.pH


    def start(self):
        # Create a separate thread for the graph
        threading.Thread(target=self._read_continuous, daemon=True).start()

    def stop(self):
        self.stop_event.set()


    def getvalues(self):
        with self.lock:
            return self.buffer if hasattr(self, 'buffer') else []
        
    def _read_continuous(self):
        self.reading    = True
        self.buffer     = []
        self.timestamps = []
        self.frameNums  = []

        while self.reading:
            if self.stop_event.is_set():
                break

            
            with self.lock:
                pH_value = self.read()            
                
                if pH_value is not None:
                    self.buffer.append(pH_value)
                    self.timestamps.append(time.time())
                    self.frameNums.append(self.Andor.frameIdx)

            time.sleep(self.interval)                

        self.reading = False
        self.stop_event.clear()

    @property
    def comports(self):
        return [port.device for port in list_ports.comports()]


    