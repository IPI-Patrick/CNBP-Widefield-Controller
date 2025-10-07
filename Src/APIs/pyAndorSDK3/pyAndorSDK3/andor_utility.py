from .andor_sdk3_exceptions import ATUtilityException, ErrorCodes


class ATUtility:
    class __ATUtility:

        __version__ = '0.1'
        LIBRARY_NAME = 'atutility'

        def __init__(self):
            from cffi import FFI
            self.ffi = FFI()
            self.ffi.set_unicode(True)
            self.C = self.ffi.cdef("""
            typedef long long AT_64;
            typedef unsigned char AT_U8;
            typedef wchar_t AT_WC;
            typedef int AT_H;

            int AT_ConvertBuffer(AT_U8* inputBuffer, AT_U8* outputBuffer, AT_64 width, AT_64 height, AT_64 stride, const AT_WC * inputPixelEncoding, const AT_WC * outputPixelEncoding);
            int AT_ConvertBufferUsingMetadata(AT_U8* inputBuffer, AT_U8* outputBuffer, AT_64 imagesizebytes, const AT_WC * outputPixelEncoding);

            int AT_GetWidthFromMetadata(AT_U8* inputBuffer, AT_64 imagesizebytes, AT_64* width);
            int AT_GetHeightFromMetadata(AT_U8* inputBuffer, AT_64 imagesizebytes, AT_64* height);
            int AT_GetStrideFromMetadata(AT_U8* inputBuffer, AT_64 imagesizebytes, AT_64* stride);
            int AT_GetPixelEncodingFromMetadata(AT_U8* inputBuffer, AT_64 imagesizebytes, AT_WC* pixelEncoding, AT_U8 pixelEncodingSize);
            int AT_GetTimeStampFromMetadata(AT_U8* inputBuffer, AT_64 imagesizebytes, AT_64* timeStamp);
            int AT_GetIRIGFromMetadata(AT_U8* inputBuffer, AT_64 imagesizebytes, AT_64* seconds, AT_64* minutes, AT_64* hours, AT_64* days, AT_64* years);
            int AT_GetExtendedIRIGFromMetadata(AT_U8* inputBuffer, AT_64 imagesizebytes, AT_64 clockfrequency, double* nanoseconds, AT_64* seconds, AT_64* minutes, AT_64* hours, AT_64* days, AT_64* years);

            int AT_InitialiseUtilityLibrary();
            int AT_FinaliseUtilityLibrary();

            """)

            self.lib = self.ffi.dlopen(self.LIBRARY_NAME)

            self.handle_return(self.lib.AT_InitialiseUtilityLibrary())

        def __del__(self):
            self.handle_return(self.lib.AT_FinaliseUtilityLibrary())

        def handle_return(self, ret_value):
            if ret_value != ErrorCodes.AT_SUCCESS:
                raise ATUtilityException(ret_value)
            return ret_value

        def unpack(self, input_buf, output_buf, width, height, stride,
                   in_format, out_format):
            ret = self.lib.AT_ConvertBuffer(
                    self.ffi.cast("AT_U8 *", input_buf),
                    self.ffi.cast("AT_U8 *", output_buf), width, height,
                    stride, in_format, out_format)
            self.handle_return(ret)

        def getWidthFromMetadata(self, input_buf, imagesizebytes):
            """
            @return: width
            """
            result = self.ffi.new("AT_64 *")
            ret = self.lib.AT_GetWidthFromMetadata(
                    self.ffi.cast("AT_U8 *", input_buf),
                    imagesizebytes, result)
            self.handle_return(ret)
            return result[0]

        def getHeightFromMetadata(self, input_buf, imagesizebytes):
            """
            @return: height
            """
            result = self.ffi.new("AT_64 *")
            ret = self.lib.AT_GetHeightFromMetadata(
                    self.ffi.cast("AT_U8 *", input_buf),
                    imagesizebytes, result)
            self.handle_return(ret)
            return result[0]

        def getStrideFromMetadata(self, input_buf, imagesizebytes):
            """
            @return: stride
            """
            result = self.ffi.new("AT_64 *")
            ret = self.lib.AT_GetStrideFromMetadata(
                    self.ffi.cast("AT_U8 *", input_buf),
                    imagesizebytes, result)
            self.handle_return(ret)
            return result[0]

        def getPixelEncodingFromMetadata(self, input_buf, imagesizebytes):
            """
            @return: pixelEncoding
            """
            pixelEncodingSize = 32
            result = self.ffi.new("AT_WC [%s]" % pixelEncodingSize)
            ret = self.lib.AT_GetPixelEncodingFromMetadata(
                    self.ffi.cast("AT_U8 *", input_buf), imagesizebytes,
                    result, self.ffi.cast("AT_U8 ", pixelEncodingSize))
            self.handle_return(ret)
            return self.ffi.string(result)

        def getTimeStampFromMetadata(self, input_buf, imagesizebytes):
            """
            @return: timeStamp
            """
            result = self.ffi.new("AT_64 *")
            ret = self.lib.AT_GetTimeStampFromMetadata(
                self.ffi.cast("AT_U8 *", input_buf), imagesizebytes, result)
            self.handle_return(ret)
            return result[0]

        def getIRIGDataFromMetadata(self, input_buf, imagesizebytes):
            """
            @return: seconds, minutes, hours, days, years
            """
            seconds = self.ffi.new("AT_64 *")
            minutes = self.ffi.new("AT_64 *")
            hours = self.ffi.new("AT_64 *")
            days = self.ffi.new("AT_64 *")
            years = self.ffi.new("AT_64 *")
            ret = self.lib.AT_GetIRIGFromMetadata(
                    self.ffi.cast("AT_U8 *", input_buf), imagesizebytes,
                    seconds, minutes, hours, days, years)
            self.handle_return(ret)
            return seconds[0], minutes[0], hours[0], days[0], years[0]

        def getExtendedIRIGDataFromMetadata(self, input_buf, imagesizebytes,
                                            clockfrequency):
            """
            @return: nanoseconds, seconds, minutes, hours, days, years
            """
            nanoseconds = self.ffi.new("double *")
            seconds = self.ffi.new("AT_64 *")
            minutes = self.ffi.new("AT_64 *")
            hours = self.ffi.new("AT_64 *")
            days = self.ffi.new("AT_64 *")
            years = self.ffi.new("AT_64 *")
            ret = self.lib.AT_GetExtendedIRIGFromMetadata(
                    self.ffi.cast("AT_U8 *", input_buf), imagesizebytes,
                    clockfrequency, nanoseconds, seconds, minutes, hours,
                    days, years)
            self.handle_return(ret)
            return (nanoseconds[0], seconds[0], minutes[0],
                    hours[0], days[0], years[0])

    instance = None

    def __init__(self):
        if not ATUtility.instance:
            ATUtility.instance = ATUtility.__ATUtility()

    def __getattr__(self, name):
        return getattr(self.instance, name)
