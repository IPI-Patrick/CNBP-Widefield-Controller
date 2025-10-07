"""Setup module for Andor SDK3.

"""

# Always prefer setuptools over distutils
from setuptools import setup
from codecs import open
from os import path
import sys
import platform
from distutils import dir_util


here = path.abspath(path.dirname(__file__))
_package_name = 'pyAndorSDK3'


def get_max_bits():
    if (sys.maxsize > 2**32):
        return '64'
    else:
        return '32'


with open(path.join(here, 'README.rst'), encoding='utf-8') as f:
    long_description = f.read()

version_ns = {}
with open(path.join(here, _package_name, '_version.py')) as f:
    exec(f.read(), {}, version_ns)

destination_path = path.join(here, _package_name, 'libs')
source_lib_path = path.join(here, _package_name, 'libs',
                            platform.system(), get_max_bits())
dir_util.copy_tree(source_lib_path, destination_path, preserve_mode=0)


setup(
    name=_package_name,
    version=version_ns['__version__'],
    description='Provides a wrapper for the Andor SDK3 API',
    long_description=long_description,
    url='',
    author='Andor sCMOS SDK3 team',
    author_email='productsupport@andor.com',
    license='ANDOR TECHNOLOGY LTD',
    platforms=['Windows', 'Linux'],

    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Build Tools',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Programming Language :: Python :: 3',
        'Operating System :: Microsoft :: Windows',
        'Operating System :: POSIX :: Linux',
    ],

    packages=[_package_name],
    package_dir={_package_name: _package_name},
    package_data={_package_name: ["libs/*.dll"]},

    install_requires=['cffi', 'numpy'],
    extras_require={
        'save': ['astropy'],
        'show': ['matplotlib']},
    zip_safe=False
)
