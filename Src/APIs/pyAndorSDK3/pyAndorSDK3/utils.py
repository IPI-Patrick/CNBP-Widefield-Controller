
import platform
import os


class CaseInsensitiveDict:
    def __init__(self):
        self.__store = dict()

    def __setitem__(self, key, value):
        self.__store[key.lower()] = value

    def __getitem__(self, key):
        return self.__store[key.lower()]

    def items(self):
        return self.__store.items()

    def __contains__(self, item):
        return item.lower() in self.__store


def lazy_prop(fn, *args):
    attr_name = '_' + fn.__name__ + '_thunk'

    @property
    def _lazyprop(self):
        if not hasattr(self, attr_name):
            setattr(self, attr_name, fn(self, *args))
        return getattr(self, attr_name)
    return _lazyprop


def dyn_prop(fn, *args):
    @property
    def _dyn_prop(self):
        return fn(self, *args)
    return _dyn_prop


def add_library_path(path):
    """
    During installation the appropriate libraries (64/32 bit) are copied into
    the site-packages folder of your python installation from
    pyAndorSDK3/pyAndorSDK3/libs.

    When the pyAndorSDK3 module is imported the site-packages folder containing
    the libs is added to the systems PATH variable.
    To manually specify a location to search for libraries use this function.

    Note
    ----
    Windows Only

    Parameters
    ----------
    path : string
       A path to the directory containing libraries desired for use

    Examples
    --------
    >>> from pyAndorSDK3 import utils
    >>> utils.add_library_path(r'path\to\directory\containing\libraries')

    Raises
    ------
    NotImplementedError
        Raises NotImplementedError when this function is used outside
        of Windows
    """
    if platform.system() != 'Windows':
        err_str = 'Adding library path is a Windows only feature.'
        raise NotImplementedError(err_str)

    _path = '{};{}'.format(path, os.environ['PATH'])
    os.environ['PATH'] = _path
