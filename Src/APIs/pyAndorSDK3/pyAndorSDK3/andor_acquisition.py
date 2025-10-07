import numpy as np

try:
    import matplotlib.pyplot as plt
except ImportError:
    can_show = False
else:
    can_show = True

try:
    from astropy.io import fits
except ImportError:
    can_save = False
else:
    can_save = True

from .utils import lazy_prop
from .andor_utility import ATUtility


class Acquisition:
    """Each Acquisition object represents 1 image.

    Attributes
    ----------
    image : ndarray
        The image data arranged into a 2D numpy array based on the image AOI
        and PixelEncoding settings
    raw_data : npdarray
        The raw image data numpy array. Note: this is a copy of the data.
    metadata : Metadata object
        Object for the image metadata - See Metadata Class for more info
    """

    def __init__(self, np_data, config):
        self._atutil = ATUtility()
        self._np_data = np_data
        self._config = config

        if config['MetadataEnable']:
            self.metadata = Metadata(np_data, config, self._atutil)

    @property
    def raw_data(self):
        return np.copy(self._np_data)

    @lazy_prop
    def image(self):
        np_d = self.__correct_for_encoding(
            self._np_data[0: self._config['aoiheight'] *
                          self._config['aoistride']])
        np_d = np_d.reshape(self._config['aoiheight'],
                            np_d.size//self._config['aoiheight'])
        return np_d[0:self._config['aoiheight'], 0:self._config['aoiwidth']]

    def __correct_for_encoding(self, np_arr):
        if self._config["pixelencoding"].lower() in ("mono12", "mono16"):
            return np_arr.view(dtype='H')
        elif self._config["pixelencoding"].lower() == "mono32":
            return np_arr.view(dtype='I')
        elif self._config["pixelencoding"].lower() == "mono12packed":
            if not hasattr(self, "_np_unpacked"):
                self._np_unpacked = np.empty(
                    (self._config['aoiheight'] * self._config['aoiwidth'] * 2),
                    dtype='B')
                self._atutil.unpack(
                    np_arr.ctypes.data, self._np_unpacked.ctypes.data,
                    self._config['aoiwidth'], self._config['aoiheight'],
                    self._config['aoistride'], "Mono12Packed", "Mono16")
            return self._np_unpacked.view(dtype='H')

    def save(self, path, overwrite_if_exist=False):
        """Saves the current Acquisition instance image data to fits file

        Parameters
        ----------
        path : str
            The destination file path (including filename) to save to
        overwrite_if_exist : bool, default=False
            Flag to overwrite the file name if it already exists

        Raises
        ------
        NotImplementedError
            Raised if the 'save' package is not installed
        OSError
            Raised when save destination path already exists but have not set
            overwrite_if_exists to True
        """

        if not can_save:
            raise NotImplementedError(
                'Please install the extra package "save".')
        if path.split('.')[-1] == 'fits':
            hdu = fits.PrimaryHDU(self.image)
            hdulist = fits.HDUList([hdu])
            for k, v in self._config.items():
                try:
                    hdulist[0].header["HIERARCH " + k.upper()] = v
                except Exception:
                    pass

            hdulist.writeto(path, overwrite=overwrite_if_exist)
        else:
            raise NotImplementedError

    def show(self, cmap="Greys_r", image_name=None):
        """Opens a Matplotlib graph to disply the image

        Parameters
        ----------
        cmap : str, default="Greys_r"
            The cmap parameter for plt.imshow() - see matplotlibs cmap for
            more information
        image_name : optional
            The title of the image to display

        Raises
        ------
        NotImplementedError
            Raised if the 'show' package is not installed
        """
        if not can_show:
            err_str = 'Please install the extra package "show".'
            raise NotImplementedError(err_str)
        plt.imshow(np.fliplr(np.rot90(self.image, 3)), cmap=cmap)
        if image_name is not None:
            plt.title(image_name)
        plt.show()


class Metadata(object):
    """The Metadata accessor class for the image

    Note
    ----
    All irig_x attributes are only available if camera has MetadataIRIG feature

    Attributes
    ----------
    timestamp
    width
    height
    stride
    pixelencoding
    irig_nanoseconds
        Only available if camera has feature IRIGClockFrequency
    irig_seconds
    irig_minutes
    irig_hours
    irig_days
    irig_years
    """

    def __init__(self, np_data, config, atutil):
        self._np_data = np_data
        self._config = config
        self._atutil = atutil

        if self._config['MetadataTimestamp']:
            self.timestamp = self.__get_timestamp()

        if "MetadataIRIG" in self._config and self._config['MetadataIRIG']:
            if 'IRIGClockFrequency' in self._config:
                (self.irig_nanoseconds, self.irig_seconds,
                 self.irig_minutes, self.irig_hours, self.irig_days,
                 self.irig_years) = self.__get_extended_irig()
            else:
                (self.irig_seconds, self.irig_minutes, self.irig_hours,
                 self.irig_days, self.irig_years) = self.__get_irig()
                self.irig_nanoseconds = 0

        if self._config['MetadataFrameInfo']:
            self.width = self.__get_width()
            self.height = self.__get_height()
            self.stride = self.__get_stride()
            self.pixelencoding = self.__get_pixel_encoding()

    def __get_timestamp(self):
        return self._atutil.getTimeStampFromMetadata(
            self._np_data.ctypes.data, self._config['imagesizebytes'])

    def __get_extended_irig(self):
        return self._atutil.getExtendedIRIGDataFromMetadata(
            self._np_data.ctypes.data, self._config['imagesizebytes'],
            self._config['irigclockfrequency'])

    def __get_irig(self):
        return self._atutil.getIRIGDataFromMetadata(
            self._np_data.ctypes.data, self._config['imagesizebytes'])

    def __get_width(self):
        return self._atutil.getWidthFromMetadata(
            self._np_data.ctypes.data, self._config['imagesizebytes'])

    def __get_height(self):
        return self._atutil.getHeightFromMetadata(
            self._np_data.ctypes.data, self._config['imagesizebytes'])

    def __get_stride(self):
        return self._atutil.getStrideFromMetadata(
            self._np_data.ctypes.data, self._config['imagesizebytes'])

    def __get_pixel_encoding(self):
        return self._atutil.getPixelEncodingFromMetadata(
            self._np_data.ctypes.data, self._config['imagesizebytes'])
