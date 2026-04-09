# Author:           Zimo Zhao
# E-mail:           zimo.zhao@eng.ox.ac.uk
# Copyright         2023

import os
import logging
import time
import inspect
import glob
import zipfile

class GlobalLogger():
    def __init__(self):
        pass

    @staticmethod    
    def logger_init(LogLevel: int = logging.INFO) -> logging.Logger:
        """Initialize a global logger for the software to keep a record of the run-time status and exceptions

        :param LogLevel: The logging level, defaults to logging.INFO
        :type LogLevel: int, optional
        :return: The global logger
        :rtype: logging.Logger
        """
        logFormatter = logging.Formatter(
            '%(asctime)s %(levelname)-8s %(message)-80s [%(filename)s:%(funcName)s:%(lineno)d] ', datefmt='%Y-%m-%d:%H:%M:%S')
        
        # Check the existence of Log folder, create one if not
        logFilePath=os.path.join(os.path.dirname(inspect.getmodule(inspect.stack()[1][0]).__file__),'Log')
        os.makedirs(logFilePath,exist_ok=True)
        
        # Zip-pack the log files if it has more than 10 log files in the folder
        logFiles=glob.glob(os.path.join(logFilePath,'*.log'))
        if len(logFiles)>=10:
            zipFileName=os.path.splitext(os.path.split(logFiles[0])[1])[0]+'-'+os.path.splitext(os.path.split(logFiles[-1])[1])[0]+'.zip'
            with zipfile.ZipFile(os.path.join(logFilePath,zipFileName), 'w') as zipObj:
            # Iterate over all the files in directory
                for folderName, _, filenames in os.walk(logFilePath):
                    for filename in filenames:
                        if 'log'in filename:
                            # create complete filepath of file in directory
                            filePath = os.path.join(folderName, filename)
                            # Add file to zip
                            zipObj.write(filePath,os.path.basename(filePath))
            _=[os.remove(_path) for _path in logFiles]
            
        # Create a log file basing on the current time.
        currenttime=time.gmtime()

        logFileName = os.path.join(logFilePath,time.strftime("%Y%m%d_%H%M%S",currenttime)+'.log')

        # Setup File handler
        fileHandler = logging.FileHandler(logFileName)
        fileHandler.setFormatter(logFormatter)
        fileHandler.setLevel(LogLevel)

        # Setup Stream Handler (i.e. console)
        streamHandler = logging.StreamHandler()
        streamHandler.setFormatter(logFormatter)
        streamHandler.setLevel(LogLevel)

        # Get our logger
        logger = logging.getLogger()
        logger.setLevel(LogLevel)

        # Add both Handlers
        logger.addHandler(fileHandler)
        logger.addHandler(streamHandler)

        logger.info('--------------------- '+time.strftime("%a, %d %b %Y %H:%M:%S %z",currenttime)+' ---------------------')
        return logger

# #### END OF CODE #### #
