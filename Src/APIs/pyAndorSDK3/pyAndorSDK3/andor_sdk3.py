class AndorSDK3:
    """Object to represent SDK3 itself
    Use this to get access to Cameras

    Attributes
    ----------
    cameras : list of Camera objects
        A list of camera objects found by pyAndorSDK3
    DeviceCount : int
        Number of cameras found by SDK3
    SoftwareVersion
        The Software Version of SDK3
    """

    AT_HANDLE_SYSTEM = 1

    def __init__(self):
        from .andor_sdk3_internals import ATCore
        self._lib = ATCore()

    @property
    def cameras(self):
        from .andor_camera import Camera
        cam_list = []
        for i in range(self.DeviceCount):
            cam_list.append(Camera(self._lib, i))
        return cam_list

    def Reinitialise(self):
        """Reinitialise SDK3 by calling the finalise and initalise commands

        Raises
        ------
        ATCoreException
            Raised if an error occurs
        """

        self._lib.finalise()
        self._lib.initialise()

    @property
    def DeviceCount(self):
        return self._lib.get_int(self.AT_HANDLE_SYSTEM, "DeviceCount")

    @property
    def SoftwareVersion(self):
        return self._lib.get_string(self.AT_HANDLE_SYSTEM, "SoftwareVersion")

    def GetCamera(self, i):
        """Returns a Camera object for the camera at the index provided"""
        from .andor_camera import Camera
        return Camera(self._lib, i)

    def event_callback(self, func):
        """Creates an event callback function to be used with callback
        registering. Best used as a decorator

        Parameters
        ----------
        func : callable
            The callback function to execute.
            Func must be created to accept two parameters. (One parameter is
            deprecated).
            The first parameter will be the handle of the camera and the second
            the FeatureName.

        Example
        -------
        >>> @sdk3.event_callback  #  used as a decorator
        >>> def callback_func(handle, feature):
        ...     val = "callback handle: {}  feature: {}  value: {}"
        ...     print(val.format(handle, feature, getattr(cam, feature)))
        ...
        ...
        >>> cam.register_feature_callback("ExposureTime", callback_func)
        """
        from functools import wraps
        from cffi import FFI
        from inspect import signature
        ffi = FFI()
        ffi.set_unicode(True)
        num_params = len(signature(func).parameters)
        if num_params == 1:
            @wraps(func)
            def _func(handle, result, ctxt):
                func(ffi.string(result))
                return 0
        elif num_params == 2:
            @wraps(func)
            def _func(handle, result, ctxt):
                func(handle, ffi.string(result))
                return 0
        else:
            err_str = "Callback function '{}' requires, at most, 2 " +\
                      " parameters but {} were given"
            raise TypeError(err_str.format(func.__name__, num_params))
        return self._lib.build_callback(_func)
