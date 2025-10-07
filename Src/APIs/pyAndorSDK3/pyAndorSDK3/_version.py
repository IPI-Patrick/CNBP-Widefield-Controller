# major should never change
# minor = changes within code - to update run "version_updater.py --minor"
# build = updates to dlls - to update run "version_updater.py --build"

major = 1
minor = 22
build = 6
__version_info__ = (major, minor, build)
__version__ = '.'.join(map(str, __version_info__))