"""Loader + facade for the fully GIL-free C++/CUDA acquisition engine (fastacq).

fastacq runs the entire capture->process loop in a C++ thread (no GIL), so the
Dear PyGui renderer cannot interfere with it. This module:
  * adds the matching CUDA-13 toolkit DLL directory to the search path (the
    machine may have several CUDA toolkits; fastacq is built against v13 and
    needs cufft64_12.dll from it),
  * imports the compiled extension if present,
  * exposes ``available()`` and the ``fastacq`` module objects.

Build the extension with src/APIs/fastacq/build.bat. If it is absent or the
toolkit DLLs cannot be found, ``available()`` returns False and the app uses the
existing Python/CuPy pipeline.
"""
import os
import sys
import glob

_MODULE = None
_AVAILABLE = None
_FASTACQ_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "APIs", "fastacq")
_CUDA_ROOT = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"


def _add_cuda_dll_dirs():
    """Add the newest CUDA v13.* toolkit bin dirs to the DLL search path."""
    added = []
    # Prefer an explicit v13 toolkit (fastacq links cufft64_12 from CUDA 13).
    candidates = sorted(glob.glob(os.path.join(_CUDA_ROOT, "v13.*")), reverse=True)
    cuda_path = os.environ.get("CUDA_PATH")
    if cuda_path and os.path.basename(cuda_path).startswith("v13"):
        candidates.insert(0, cuda_path)
    for toolkit in candidates:
        for sub in (("bin", "x64"), ("bin",)):
            d = os.path.join(toolkit, *sub)
            if os.path.isdir(d):
                try:
                    os.add_dll_directory(d)
                    added.append(d)
                except Exception:
                    pass
    return added


def available():
    """True if the compiled fastacq extension loads on this machine."""
    global _MODULE, _AVAILABLE
    if _AVAILABLE is None:
        _AVAILABLE = False
        try:
            _add_cuda_dll_dirs()
            if _FASTACQ_DIR not in sys.path:
                sys.path.insert(0, _FASTACQ_DIR)
            import fastacq as _fa
            _MODULE = _fa
            _AVAILABLE = True
        except Exception as exc:
            print(f"fastacq acquisition engine unavailable ({exc}); using CuPy path.")
    return _AVAILABLE


def module():
    """Return the loaded fastacq module (or None)."""
    return _MODULE if available() else None
