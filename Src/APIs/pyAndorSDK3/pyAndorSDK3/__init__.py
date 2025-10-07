__title__ = 'pyAndorSDK3'

__authors__ = 'Andor SDK3 team'
__email__ = "scmossupport@andor.com"

__license__ = 'Andor'
__copyright__ = 'Copyright 2021 Andor'

import os
from ._version import __version__, __version_info__

from .andor_sdk3 import AndorSDK3
from .andor_sdk3_exceptions import CameraException
from .andor_sdk3_exceptions import ATCoreException
from .andor_sdk3_exceptions import ErrorCodes
from .andor_acquisition import Acquisition

_path = os.path.dirname(__file__) + '/libs;' + os.environ['PATH']
os.environ['PATH'] = _path

__all__ = [
    'AndorSDK3', 'Acquisition', 
    'CameraException', 'ATCoreException', 'ErrorCodes',
    '__title__', '__authors__', '__email__',
    '__license__', '__copyright__', '__version__', '__version_info__',
]
