from enum import IntEnum, unique


@unique
class ErrorCodes(IntEnum):
    """SDK3 AT Error Codes"""

    # ATCore Codes
    AT_SUCCESS = 0
    AT_ERR_NOTINITIALISED = 1
    AT_ERR_NOTIMPLEMENTED = 2
    AT_ERR_READONLY = 3
    AT_ERR_NOTREADABLE = 4
    AT_ERR_NOTWRITABLE = 5
    AT_ERR_OUTOFRANGE = 6
    AT_ERR_INDEXNOTAVAILABLE = 7
    AT_ERR_INDEXNOTIMPLEMENTED = 8
    AT_ERR_EXCEEDEDMAXSTRINGLENGTH = 9
    AT_ERR_CONNECTION = 10
    AT_ERR_NODATA = 11
    AT_ERR_INVALIDHANDLE = 12
    AT_ERR_TIMEDOUT = 13
    AT_ERR_BUFFERFULL = 14
    AT_ERR_INVALIDSIZE = 15
    AT_ERR_INVALIDALIGNMENT = 16
    AT_ERR_COMM = 17
    AT_ERR_STRINGNOTAVAILABLE = 18
    AT_ERR_STRINGNOTIMPLEMENTED = 19
    AT_ERR_NULL_FEATURE = 20
    AT_ERR_NULL_HANDLE = 21
    AT_ERR_NULL_IMPLEMENTED_VAR = 22
    AT_ERR_NULL_READABLE_VAR = 23
    AT_ERR_NULL_READONLY_VAR = 24
    AT_ERR_NULL_WRITABLE_VAR = 25
    AT_ERR_NULL_MINVALUE = 26
    AT_ERR_NULL_MAXVALUE = 27
    AT_ERR_NULL_VALUE = 28
    AT_ERR_NULL_STRING = 29
    AT_ERR_NULL_COUNT_VAR = 30
    AT_ERR_NULL_ISAVAILABLE_VAR = 31
    AT_ERR_NULL_MAXSTRINGLENGTH = 32
    AT_ERR_NULL_EVCALLBACK = 33
    AT_ERR_NULL_QUEUE_PTR = 34
    AT_ERR_NULL_WAIT_PTR = 35
    AT_ERR_NULL_PTRSIZE = 36
    AT_ERR_NOMEMORY = 37
    AT_ERR_DEVICEINUSE = 38
    AT_ERR_HARDWARE_OVERFLOW = 100

    #AT_Utility Codes
    AT_ERR_INVALIDOUTPUTPIXELENCODING = 1002
    AT_ERR_INVALIDINPUTPIXELENCODING = 1003
    AT_ERR_INVALIDMETADATAINFO = 1004
    AT_ERR_CORRUPTEDMETADATA = 1005
    AT_ERR_METADATANOTFOUND = 1006
    AT_ERR_INVALIDFORMAT = 1008
    AT_ERR_INVALIDPATH = 1009
    AT_ERR_NO_NEW_DATA = 1010
    AT_ERR_SPOOLING_NOT_CONFIGURED = 1011


class ATException(Exception):
    """AT base Exception class

    Attributes
    ----------
    err_code : int
        The AT error code returned

        Can ErrorCodes enum to handle specific Exceptions
        See error_handling.py example
    err_str : str
        The error string corrosponding to the error code
    """
    def __init__(self, err_code, message=""):
        try:
            self.err_code = ErrorCodes(err_code)
            self.err_str = self.err_code.name
        except:
            self.err_code = err_code
            self.err_str = "Unknown Error"
            
        msg = "{} ({})".format(err_code, self.err_str)
        if message != "":
            msg = "{} - {}".format(message, msg)
        super().__init__(msg)

    def _get_message(self):
        return str(self)


class ATCoreException(ATException):
    """Typically thrown by andor_sdk3_internals

    Attributes
    ----------
    err_code : int
        The AT error code returned

        Can ErrorCodes enum to handle specific Exceptions
        See error_handling.py example
    err_str : str
        The error string corrosponding to the error code
    """
    def __init__(self, err_code, message=""):
        super().__init__(err_code, message)


class CameraException(ATCoreException):
    """Exception raised by Camera class

    Attributes
    ----------
    err_code : int
        The AT error code returned

        Can use ErrorCodes enum to handle specific Exceptions.
        See error_handling.py example.
    err_str : str
        The error string corrosponding to the error code"""
    pass


class ATUtilityException(ATException):
    """Exception raised by ATUtility class

    Attributes
    ----------
    err_code : int
        The AT error code returned

        Can use ErrorCodes enum to handle specific Exceptions.
        See error_handling.py example.
    err_str : str
        The error string corrosponding to the error code"""
    def __init__(self, err_code, message=""):
        super().__init__(err_code, message)