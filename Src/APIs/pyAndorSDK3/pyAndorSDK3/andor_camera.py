from functools import wraps
from queue import Queue
import time
from math import ceil

import numpy as np
from collections import deque

from .utils import CaseInsensitiveDict
from .andor_acquisition import Acquisition
from .andor_sdk3_exceptions import ATCoreException, CameraException


class Feature:
    def __init__(self, func, execute=False):
        self.func = func
        self.execute = execute


class Camera:
    """Each `Camera` object represents a single camera.

    In the below Attributes section where "FeatureName" is listed is just a
    reference and real features are to be used its place - consider the
    examples provided.

    Attributes
    ----------
    FeatureName : any
        Access to features - used for both setting and getting feature values.

        e.g. cam.CycleMode = "Fixed"

        e.g. img_size = cam.ImageSizeBytes

        Command features MUST be executed like a function.
        e.g. cam.AcquisitionStart()
    min_FeatureName : int or float
        The minimum value this feature can set.

        e.g. cam.ExposureTime = cam.min_ExposureTime
    max_FeatureName : int or float
        The max value this feature can set.

        e.g. cam.FrameRate = cam.max_FrameRate
    options_FeatureName : list of str
        List of legal values for a feature - to be used with Enum features.
        Note: although a value may be legal for that feature it may not be
        available to set at that time - use `is_available_` to confirm the
        availability of a value.

        e.g. legal_values = cam.options_PixelEncoding
    is_available_FeatureName : bool
        Returns the availability of the specified legal value on an Enum
        feature.

        e.g. avail = cam.is_available_PixelEncoding("Mono16")
    available_options_FeatureName : list of str
        List of legal values for the Feature that are also available.
    type_FeatureName : str
        Returns the feature type as a str - can be "int", "float", "bool",
        "enumerated_string", "string" or "command".
    """

    __feature_funcs = ["min_", "max_", "options_", "is_available_", "type_",
                       "available_options_"]
    __types_to_check = ["int", "float", "bool", "enumerated_string", "string"]
    __func_map = {"min_": "_min", "max_": "_max", "options_": "_options"}

    __registered_callbacks = None
    __orig_reg_vals = None
    __features_type = None
    __queued_buffers = None
    __current_config = None
    __prev_acq = None

    _lib = None
    _index = None
    _handle = 0

    def __init__(self, lib, index):
        self.__registered_callbacks = []
        self.__orig_reg_vals = {}
        self.__features_type = {}
        self.__queued_buffers = Queue()
        self.__current_config = CaseInsensitiveDict()
        self.__prev_acq = []

        self._lib = lib
        self._index = index
        self._handle = 0
        self.open()
        if self._lib.get_bool(self._handle, "CameraAcquiring"):
            self.AcquisitionStop()
        self.flush()
        self.__populate_config()

    def camera(self):
        """Returns the current object.

        Note
        ----
        Deprecated - Exists for backwards compatibility.
        """

        return self

    def __destroy_cam(self):
        self.close()

    def __del__(self):
        self.__destroy_cam()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        for a, b in self.__registered_callbacks:
            self.unregister_feature_callback(a, b)
        self.__registered_callbacks = []
        self.__destroy_cam()

    @property
    def lib(self):
        """Access to the Andor lib functions (listed in andor_sdk3_internals).

        Note
        ----
        Not recommended to use this to access functions.
        """

        return self._lib

    @property
    def handle(self):
        """Returns the camera handle."""
        return self._handle

    @property
    def index(self):
        """Returns the index of the camera."""
        return self._index

    def open(self):
        """Opens the camera associated with this `Camera` object at the index
        indicated.

        Note
        ----
        It is not necessary to call this function as in normal operation
        the opening/closing of the camera is handled automatically.

        If you have manually called close(), open() can be called to
        reopen the camera associated with this `Camera` object.

        Raises
        ------
        CameraException
            Raises an exception with an SDK3 error code if open fails.
        """

        if self._handle == 0:
            try:
                self._handle = self._lib.open(self._index)
            except ATCoreException as e:
                err_str = "Error Opening Camera Index {}".format(self._index)
                raise CameraException(e.err_code, err_str)

    def close(self):
        """Closes the camera associated with this `Camera` object.

        Note: it is not necessary to call this function as in normal operation
        the opening/closing of the camera is handled automatically.

        If you have manually called close(), open() can be called to reopen
        the camera associated with this `Camera` object.

        Raises
        ------
        ATCoreException
            Raises an exception with an SDK3 error code if close fails.
        """

        if self._handle != 0:
            self._lib.close(self._handle)
            self._handle = 0

    def __raise_attribute_error(self, feature_name):
        err_str = "Camera has no Feature '{}'".format(feature_name)
        raise AttributeError(err_str)

    def __create_func(self, command, feature_name):
        @wraps(command)
        def f(*args, **kwargs):
            try:
                return command(self._handle, feature_name, *args, **kwargs)
            except ATCoreException as e:
                err_str = feature_name + ("%s" % str(args))
                raise CameraException(e.err_code, err_str)
        return f

    def __setup_feature_type(self, feature_name):
        if feature_name not in self.__features_type:
            if not self._lib.is_implemented(self._handle, feature_name):
                self.__raise_attribute_error(feature_name)

            for feature_type in self.__types_to_check:
                try:
                    get_func = getattr(self._lib, "get_"+feature_type)
                    get_func(self._handle, feature_name)
                    self.__features_type[feature_name] = feature_type
                    return
                except ATCoreException as e:
                    if ("AT_ERR_NOTIMPLEMENTED" not in str(e)):
                        self.__features_type[feature_name] = feature_type
                        return
            self.__features_type[feature_name] = "command"

    def __get_feature_available_options(self, handle, feature_name):
        option_list = []
        for option in getattr(self, "options_"+feature_name):
            if getattr(self, "is_available_"+feature_name)(option):
                option_list.append(option)
        return option_list

    def __create_feature_func(self, begin_text, feature_name):
        self.__setup_feature_type(feature_name)
        feat_type = self.__features_type[feature_name]
        if begin_text == "type_":
            return Feature(feat_type)
        else:
            try:
                exe = True
                if begin_text == "is_available_":
                    lib_func = self._lib.is_enumerated_string_available
                    exe = False
                elif begin_text == "available_options_":
                    lib_func = self.__get_feature_available_options
                else:
                    func_name = "get_"+feat_type+self.__func_map[begin_text]
                    lib_func = getattr(self._lib, func_name)
                feat_func = self.__create_func(lib_func, feature_name)
                return Feature(feat_func, exe)
            except AttributeError:
                feat_type = self.__features_type[feature_name]
                err_str = ("Cannot use the '{}' function ".format(begin_text) +
                           "for feature '{}' ".format(feature_name) +
                           "with type '{}'".format(feat_type))
                raise AttributeError(err_str)

    def __getattr__(self, feature_name):
        split_feat_name = feature_name.split("_")
        begin_text = "_".join(split_feat_name[0:-1])+"_"
        if begin_text in self.__feature_funcs:
            feat = self.__create_feature_func(begin_text, split_feat_name[-1])
        else:
            self.__setup_feature_type(feature_name)
            lib_func_name = self.__features_type[feature_name]
            if lib_func_name != "command":
                lib_func_name = "get_" + lib_func_name
            lib_func = getattr(self._lib, lib_func_name)
            get_func = self.__create_func(lib_func, feature_name)
            feat = Feature(get_func, lib_func_name != "command")
        return feat.func() if feat.execute else feat.func

    def __setattr__(self, feature_name, value):
        if feature_name in dir(self):  # is a method not a feature
            super().__setattr__(feature_name, value)
            return

        text = feature_name.split("_")[0]
        if text in self.__feature_funcs:
            err_str = "Cannot use set on Feature Function {} ".format(text)
            raise AttributeError(err_str)

        self.__setup_feature_type(feature_name)
        func = getattr(self._lib, "set_"+self.__features_type[feature_name])
        self.__create_func(func, feature_name)(value)

    def queue_buffer(self, buffer_size):
        """Creates and Queues an empty buffer of size 'buffer_size'.

        The `Camera` object can queue buffers, handle and delete any buffers
        it has queued.

        Parameters
        ----------
        buffer_size : int
            The size of the buffer to create and queue.

        Raises
        ------
        CameraException
            Raises an exception with an SDK3 error code if queue fails.
        """

        self.__queue_buffer(buffer_size)

    def __queue_buffer(self, buffer_size):
        buf = np.empty((buffer_size,), dtype='B')
        self.queue(buf, buffer_size)

    def flush(self):
        """Flushes and deletes any buffers the `Camera` object has created.

        Raises
        ------
        CameraException
            Raises an exception with an SDK3 error code if flush fails.
        """

        self.__flush()

    def __flush(self):
        try:
            self._lib.flush(self._handle)
        except ATCoreException as e:
            raise CameraException(e.err_code, "Error Flushing")
        self.__queued_buffers = Queue()

    def wait_buffer(self, timeout):
        """Waits for and returns a Acquisition object within the timeout period
        given (in ms)

        Parameters
        ----------
        timeout : int
            Time in ms that the image is given to return - once timeout is
            exceeded a CameraException is raised.

        Returns
        -------
        Acquisition object
            An Acquisition object which controls the image data.
            See Acquisition object docs for more information.

        Raises
        ------
        CameraException
            Raises an exception with an SDK3 error code if WaitBuffer
            fails e.g. 13 - AT_ERR_TIMEDOUT if timeout is exceeded.
        """

        return self.__acquire(timeout)

    def __acquire(self, timeout):
        try:
            (buf_ptr, buffer_size) = self._lib.wait_buffer(
                    self._handle, timeout=int(timeout))
        except ATCoreException as e:
            raise CameraException(e.err_code, "Error waiting for frame")
        return Acquisition(self.__queued_buffers.get(), self.__current_config)

    def AcquisitionStart(self):
        """An override of the feature AcquisitionStart used to
        populate a list of config details used for Acquisitions.
        """

        self.__populate_config()
        try:
            self._lib.command(self._handle, "AcquisitionStart")
        except ATCoreException as e:
            err_str = "Error executing AcquisitionStart"
            raise CameraException(e.err_code, err_str)

    def queue(self, buffer, buffer_size):
        """Queues the buffer with buffer_size.

        Parameters
        ----------
        buffer : ndarray
            A numpy array of ubyte with size buffer_size.
        buffer_size : int
            The size of the buffer to queue.

        Example
        -------
        >>> buffer_size = cam.ImageSizeBytes
        >>> buf = np.empty((buffer_size,), dtype='B')
        >>> cam.queue(buf, buffer_size)

        Raises
        ------
        CameraException
            Raises an exception with an SDK3 error code if queue fails.
        """

        self.__queued_buffers.put(buffer)
        try:
            self._lib.queue_buffer(
                self._handle, buffer.ctypes.data, buffer_size)
        except ATCoreException as e:
            raise CameraException(e.err_code, "Error queuing buffer")

    def get_previous_acquisition_series(self):
        """Returns the list of images from the most recent acquire_series.
        This is most useful for retrieval of images when acquire_series
        failed with an exception.

        Example
        -------
        >>> try:
        ...    acqs = cam.acquire_series(("FrameCount",5), 5000)
        >>> except:
        ...     acqs = cam.get_previous_acquisition_series()
        ...     num_acq = len(acqs)
        ...     err = "Error during acquire_series got {} imgs".format(num_acq)
        ...     print(err)
        """
        return self.__prev_acq

    def __acq_series_err(self, print_fps, frame, time_start):
        if print_fps:
            fps = frame / (time.time() - time_start + 0.00001)
            print("Effective FPS of Failed Series : {} fps".format(fps))
        self.AcquisitionStop()
        self.flush()
        return "Error on frame {}".format(frame)

    def acquire_series(self, *args, **kwargs):
        """Perform a series acquisition and return the images as a list of
        Acquisition objects.

        Parameters
        ----------
        *args : tuple of (str, Union[int, str, float, bool])
            Multiple key (str) and value (int, str, float, bool) tuple values
            can be passed in as features to set before starting acquisition
        **kwargs : any
        Keywords
            timeout : int, optional
                Time in ms that the image is given to return - once
                timeout is exceeded a CameraException is raised.
            min_buf : int, default=2
                Minimum number of buffers to queue for the acquisition.
            max_buf : int, default=25
                Maximum number of buffers to initially queue for the
                acquisition or the max number of buffers to use when doing a
                circular acquisition
            circ_buf : bool, default=false
                flag to use the queued buffers in a circular fashion - if
                enabled once the last buffer has been used the first buffer
                will be used for the next image etc. If not enabled (default)
                once the last buffer has been used it will ignore the max_buf
                value and queue more buffers until frame_count has been reached
                (or will loop forever if frame_count is not set and Continuous
                CycleMode is currently in use.
            print_frame : bool, default=false
                Flag to bring each frame after it is retrieved.
            print_fps : bool : , default=false
                Flag to print the current achieved framerate.
            print_fps_interval : int, default=1
                Used by print_fps to print every x number of frames.
            pause_after : float, default=0.0
                Time in seconds to sleep after acquiring.
            frame_count : int, optional
                The number of frames to acquire. Uses FrameCount feature value
                when in Fixed CycleMode - is recommended to use this param when
                in Continuous or will loop until manually stopped or an error
                occurs.

        Returns
        -------
        list of Acquisition objects
            A list of Acquisition objects which controls the image data.
            See Acquisition object docs for more information.

        Raises
        ------
        CameraException
            Raises an exception with an SDK3 error code if acquisition
            fails.

        Example
        -------
        Below Sets features AOIHeight to 1000, CycleMode to Fixed and
        FrameCount to 10000 - is set to use 10 buffers in a circular fashion
        and will return these 10 images in a list oldest image first.

        >>> acqs = cam.acquire_series(
        ...         ("AOIHeight",1000), ("CycleMode", "Fixed"),
        ...         (FrameCount,10000),
        ...         timeout=1000, max_buf=10, circ_buf=True)
        """
        self.__configure(*args)
        timeout = kwargs.pop('timeout', max(5000, ceil(5000 / self.FrameRate)))
        min_buf = kwargs.pop('min_buf', 2)
        max_buf = kwargs.pop('max_buf', 25)
        circ_buf = kwargs.pop('circ_buf', False)
        print_frame = kwargs.pop('print_frame', False)
        print_fps = kwargs.pop('print_fps', False)
        print_fps_interval = kwargs.pop('print_fps_interval', 1)
        pause_after = kwargs.pop('pause_after', 0.0)
        sw_trigger = self.TriggerMode == "Software"

        accumulate_available = True
        try:
            acc = self.AccumulateCount
        except AttributeError:
            accumulate_available = False

        if self.CycleMode == "Fixed":
            frame_count = kwargs.pop('frame_count', self.FrameCount)
            if accumulate_available and acc > 1:
                # if using accumulation then num_images that will be returned
                # is equal to framecount/accumulatecount
                frame_count = int(frame_count / acc)
            else:
                setattr(self, "FrameCount", frame_count)
            buf_count = max(min(max_buf, frame_count), min_buf)
        else:
            # if frame_count isn't specified while in continuous mode 
            # frame_count will default to 0 and acquire_series will acquire
            # forever
            frame_count = kwargs.pop('frame_count', 0)
            if frame_count > 0:
                buf_count = max(min(max_buf, frame_count), min_buf)
            else:
                buf_count = max(max_buf, min_buf)
        imgsize = self.ImageSizeBytes
        for _ in range(0, buf_count):
            self.queue_buffer(imgsize)

        try:
            self.AcquisitionStart()
        except ATCoreException as e:
            self.AcquisitionStop()
            self.flush()
            raise CameraException(e.err_code, "Error during AcquisitionStart")

        frame = 0
        series = deque()
        update_count = 0
        time_start = time.time()
        prev_time = time_start
        try:
            while(True):
                if self.__queued_buffers.qsize() == 0:
                    if circ_buf:
                        self.queue(series.popleft()._np_data, imgsize)
                    else:
                        self.queue_buffer(imgsize)
                if sw_trigger:
                    self.SoftwareTrigger()
                acq = self.wait_buffer(timeout)
                series.append(acq)

                if print_frame:
                    print(acq.image)
                frame += 1
                if frame_count != 0 and frame == frame_count:
                    break
                if print_fps:
                    update_count += 1
                    if update_count >= print_fps_interval:
                        prev_time = time.time()
                        fps = frame / (prev_time - time_start + 0.00001)
                        print("Effective FPS : {} fps".format(fps))
                        update_count = 0
        except ATCoreException as e:
            err_str = self.__acq_series_err(print_fps, frame, time_start)
            raise CameraException(e.err_code, err_str)
        except CameraException as e:
            err_str = self.__acq_series_err(print_fps, frame, time_start)
            raise CameraException(e.err_code, err_str)
        except Exception as e:
            err_str = self.__acq_series_err(print_fps, frame, time_start)
            raise Exception(err_str+" : "+str(e))
        finally:
            self.__prev_acq = list(series)
        if print_fps:
            fps = frame / (time.time() - time_start + 0.00001)
            print("Effective FPS of Completed Series : {} fps".format(fps))
        if(pause_after > 0):
            time.sleep(pause_after)
        self.AcquisitionStop()
        self.flush()
        return list(series)

    def acquire(self, *args, **kwargs):
        """Perform a single acquisition and return the image as an
        Acquisition object.

        Parameters
        ----------
        *args : tuple of (str, Union[int, str, float, bool])
            Multiple key (str) and value (int, str, float, bool) tuple values
            can be passed in as features to set before starting acquisition.
        **kwargs : any
        Keywords
            timeout : int, optional
                Time in ms that the image is given to return - once
                timeout is exceeded a CameraException is raised.
            min_buf : int, default=2
                Minimum number of buffers to queue for the acquisition.
            pause_after : float, default=0.0
                Time in seconds to sleep after acquiring.

        Returns
        -------
        Acquisition object
            An Acquisition object which controls the image data.
            See Acquisition object docs for more information.

        Raises
        ------
        CameraException
            Raises an exception with an SDK3 error code if acquisition
            fails.
        """

        self.__configure(*args)
        timeout = kwargs.pop('timeout', max(5000, ceil(5000 / self.FrameRate)))
        min_buf = kwargs.pop('min_buf', 2)
        pause_after = kwargs.pop('pause_after', 0.0)
        sw_trigger = self.TriggerMode == "SoftwareTrigger"
        for _ in range(0, min_buf):
            self.queue_buffer(self.ImageSizeBytes)
        try:
            self.AcquisitionStart()
        except ATCoreException as e:
            self.AcquisitionStop()
            self.flush()
            raise CameraException(e.err_code, "Error during AcquisitionStart")

        try:
            if sw_trigger:
                self.SoftwareTrigger()
            acq = self.wait_buffer(timeout)
        except ATCoreException as e:
            self.AcquisitionStop()
            self.flush()
            raise CameraException(e.err_code, "Error acquiring")
        if(pause_after > 0):
            time.sleep(pause_after)
        self.AcquisitionStop()
        self.flush()
        return acq

    def __configure(self, *args):
        for (k, v) in args:
            try:
                setattr(self, k, v)
            except Exception as e:
                str_err = "Error configuring feature {} : "+str(e)
                raise ValueError(str_err)

    def __populate_config(self, force=False):
        for k in ["AOIHeight", "AOIWidth", "AOIStride",
                  "PixelEncoding", "ImageSizeBytes", "MetadataEnable"]:
            if self._lib.is_readable(self._handle, k):
                self.__current_config[k] = getattr(self, k)

        if self.__current_config["MetadataEnable"]:
            for k in ["MetadataTimestamp", "MetadataIRIG",
                      "MetadataFrameInfo", "IRIGClockFrequency"]:
                has_k = hasattr(self, k)
                if (has_k and self._lib.is_readable(self._handle, k)):
                    self.__current_config[k] = getattr(self, k)

    def register_feature_callback(self, feature, func):
        """Register a feature to a callback function when said feature
        receives updates.

        Parameters
        ----------
        feature : str
            The feature to register the callback function to.
        func : callable
            The callback function to register for execution.

        Example
        -------
        >>> @sdk3.event_callback #callbacks need sdk3.event_callback decorator
        >>> def callback_func(handle, feature):
        ...     print(
        ...         "callback feature:{} value:{} min:{} max:{}".format(
        ...             feature,
        ...             getattr(cam, feature), getattr(cam, "min_"+feature),
        ...             getattr(cam, "max_"+feature)
        ...         )
        ...     )
        ...
        ...
        >>> print("Registering callback on FrameRate:")
        >>> cam.register_feature_callback("FrameRate", callback_func)

        Raises
        ------
        CameraException
            Raises an exception with an SDK3 error code if regsiter fails.
        """
        try:
            self._lib.register_feature_callback(self._handle, feature, func)
        except ATCoreException as e:
            err_str = "Error registering {}".format(feature)
            raise CameraException(e.err_code, err_str)
        self.__registered_callbacks.append((feature, func))

    def unregister_feature_callback(self, feature, func):
        """Unregister a callback from a feature.

        Parameters
        ----------
        feature : str
            The feature to unregister the callback function.
        func : callable
            The callback function to unregister.

        Example
        -------
        >>> @sdk3.event_callback # give callbacks sdk3.event_callback decorator
        >>> def callback_func(handle, feature):
        ...     print(
        ...         "callback feature:{} value:{} min:{} max:{}".format(
        ...             feature,
        ...             getattr(cam, feature), getattr(cam, "min_"+feature),
        ...             getattr(cam, "max_"+feature)
        ...         )
        ...     )
        ...
        ...
        >>> print("Registering callback on FrameRate:")
        >>> cam.register_feature_callback("FrameRate", callback_func)
        >>> # other code here
        >>> cam.unregister_feature_callback("FrameRate", callback_func)

        Raises
        ------
        CameraException
            Raises an exception with an SDK3 error code if unregister
            fails.
        """
        try:
            self._lib.unregister_feature_callback(self._handle, feature, func)
        except ATCoreException as e:
            err_str = "Error unregistering {}".format(feature)
            raise CameraException(e.err_code, err_str)
        self.__registered_callbacks.remove((feature, func))

    def register_write(self, addr, payload):
        """Writes the payload value(s) to addr

        Note
        ----
        For Internal use only

        Parameters
        ----------
        addr : int
            Start address of register(s) to write.
        payload : Union[int, list of int]
            Can either be a single value or a list of int values to write
            to the address specified.

        Raises
        ------
        CameraException
            Raises an exception with an SDK3 error code if write fails.
        """

        if (hasattr(payload, "__iter__") is False):
            payload = [payload, ]
        if addr not in self.__orig_reg_vals:
            self.__orig_reg_vals[addr] = self.register_read(addr, len(payload))
        self.__register_write(addr, payload)

    def __register_write(self, addr, payload):
        self.RegisterAddress = addr
        if (hasattr(payload, "__iter__") is False):
            payload = [payload, ]

        self.RegisterCount = len(payload)

        for i in range(0, len(payload)):
            self.RegisterIndex = i
            self.RegisterValue = payload[i]
            self.RegisterWrite()

    def restore_registers(self):
        """Restore the original values of registers written by register_write.

        Note
        ----
        For Internal use only

        Raises
        ------
        CameraException
            Raises an exception with an SDK3 error code if write fails.
        """

        for addr, payload in self.__orig_reg_vals.items():
            self.__register_write(addr, payload)
        self.__orig_reg_vals.clear()

    def register_read(self, addr, count=1):
        """Reads register(s) at the address specified.

        Parameters
        ----------
        addr : int
            The start address of register(s) to read.
        count : int, optional
            The number of registers to read starting from addr

        Returns
        -------
        Union[int, list of int]
            The value(s) of the read registers.
            Returns single int if only 1 register is read, otherwise returns
            a list of ints.

        Raises
        ------
        CameraException
            Raises an exception with an SDK3 error code if read fails.
        """

        self.RegisterAddress = addr
        self.RegisterCount = count
        self.RegisterRead()
        acc = np.empty((count,), dtype='I')
        for i in range(0, count):
            self.RegisterIndex = i
            acc[i] = self.RegisterValue
        if len(acc) == 1:
            return acc[0]
        else:
            return acc

    def get_updated_features(self):
        """Returns a list of feature names that has updated since the last
        open of the camera.

        Returns
        -------
        list
            list of str of feature names that have been updated.
        """

        return self._lib.get_updated_features()
