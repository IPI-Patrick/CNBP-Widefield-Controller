"""Loader + thin wrapper for the GIL-free C++/CUDA processing engine (fastproc).

fastproc runs the temporal-pipeline per-frame work (H2D + integer drift shift +
LP IIR + temporal-EMA background + difference/contrast + ROI means) inside ONE
call that RELEASES the GIL, so the Dear PyGui render loop can't starve the
camera processing thread. This module loads the compiled extension (if present)
and exposes a small ``FastBackend`` that returns the processed frame as a CuPy
array view (so the display colormap continues in CuPy) plus the ROI means.

If the extension is not built, ``available()`` returns False and callers fall
back to the pure-CuPy pipeline. Build it with src/APIs/fastproc/build.bat.
"""
import os
import sys

from Utils.gpu import xp, GPU_AVAILABLE

_MODULE = None
_AVAILABLE = None
_FASTPROC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "APIs", "fastproc")


def available():
    """True if the compiled fastproc extension is importable on a GPU system."""
    global _MODULE, _AVAILABLE
    if _AVAILABLE is None:
        _AVAILABLE = False
        if GPU_AVAILABLE:
            try:
                if _FASTPROC_DIR not in sys.path:
                    sys.path.insert(0, _FASTPROC_DIR)
                import fastproc as _fp  # compiled .pyd
                _MODULE = _fp
                _AVAILABLE = True
            except Exception as exc:
                print(f"fastproc C++ backend unavailable ({exc}); using CuPy path.")
    return _AVAILABLE


class FastBackend:
    """One engine per frame shape. Wraps the engine's device output buffer as a
    persistent CuPy view; callers should ``.copy()`` the returned frame if they
    need it stable past the next ``process`` call (the device buffer is reused)."""

    def __init__(self, height, width):
        if not available():
            raise RuntimeError("fastproc backend is not available")
        self.H = int(height)
        self.W = int(width)
        self.eng = _MODULE.Engine(self.H, self.W)
        nbytes = self.H * self.W * 4
        mem = xp.cuda.UnownedMemory(self.eng.out_ptr(), nbytes, self.eng)
        self._out_view = xp.ndarray((self.H, self.W), xp.float32,
                                    xp.cuda.MemoryPointer(mem, 0))

    def process(self, raw_u16, shift_x, shift_y, *, lp_enabled, b0, b1, a1,
                max_value, alpha, mode, roi_rects):
        """Run one frame GIL-free. ``roi_rects`` is an int32 (N,4) array of
        [y0, y1, x0, x1]. Returns ``(out_view_gpu, roi_means_list)``."""
        means = self.eng.process(
            raw_u16, int(shift_x), int(shift_y), bool(lp_enabled),
            float(b0), float(b1), float(a1), float(max_value),
            float(alpha), int(mode), roi_rects,
        )
        return self._out_view, means
