"""GPU/CPU compatibility shim.

Detects NVIDIA GPU + CuPy at import time.  All downstream code imports ``xp``
in place of ``numpy`` and calls the wrapper functions below instead of
scipy.ndimage directly — zero conditional branches are needed anywhere else.

Usage
-----
    from Utils.gpu import xp, to_gpu, to_cpu, gaussian_filter, ndimage_shift
    from Utils.gpu import phase_cross_correlation_ds

    frame = to_gpu(raw_frame_np)   # move to GPU (no-op on CPU systems)
    frame = ndimage_shift(frame, (dy, dx))
    result = to_cpu(frame)         # back to NumPy for storage / display
"""

import numpy as np
import scipy.ndimage as _sp_nd

try:
    import cupy as _cp
    import cupyx.scipy.ndimage as _cpx_nd
    _cp.cuda.runtime.getDeviceCount()   # raises CUDADriverError if no GPU
    xp = _cp
    _nd = _cpx_nd
    GPU_AVAILABLE = True
    _device = _cp.cuda.Device(0)
    _props = _cp.cuda.runtime.getDeviceProperties(_device.id)
    _gpu_name = _props["name"].decode() if isinstance(_props["name"], bytes) else str(_props["name"])
    print(f"Processing backend: GPU ({_gpu_name})")
    del _device, _props, _gpu_name
except Exception:
    xp = np
    _nd = _sp_nd
    GPU_AVAILABLE = False
    print("Processing backend: CPU (NumPy/SciPy)")


def to_gpu(arr):
    """Move *arr* to device memory.  No-op on CPU-only systems."""
    return xp.asarray(arr)


def to_cpu(arr):
    """Return a plain NumPy ndarray from any device."""
    if GPU_AVAILABLE and isinstance(arr, xp.ndarray):
        return xp.asnumpy(arr)
    return np.asarray(arr)


def gaussian_filter(arr, sigma):
    """Gaussian blur on the current compute device."""
    return _nd.gaussian_filter(arr, sigma=float(sigma))


def ndimage_shift(arr, shift, *, order=1, cval=0.0):
    """Sub-pixel shift on the current compute device."""
    return _nd.shift(arr, shift, order=order, prefilter=False, cval=cval)


def background_filter(arr, sigma):
    """Fast background estimate via uniform_filter (box-filter approximation of Gaussian).

    uniform_filter runs O(N·M) regardless of kernel size — much faster than
    gaussian_filter for large sigma, which is typical for widefield BG removal.
    A single box pass with width ≈ 3.46·sigma matches the Gaussian spread.
    """
    size = max(3, int(round(float(sigma) * 3.46)))
    if size % 2 == 0:
        size += 1
    return _nd.uniform_filter(arr, size=size)


def phase_cross_correlation_ds(reference, frame_f32, *, downsample=4, _ref_fft=None):
    """FFT phase cross-correlation on spatially downsampled images.

    The FFTs run on the compute device (GPU when available).  Only the tiny
    correlation map (~N/ds × N/ds floats) is transferred back to CPU for peak
    detection, then quadratic sub-pixel refinement is applied.  The resulting
    shift is rescaled to full-resolution pixels.

    Parameters
    ----------
    reference  : 2-D float32 ndarray — reference / zero frame (CPU)
    frame_f32  : 2-D float32 array   — current frame (GPU or CPU)
    downsample : spatial downsample factor (default 4 → 16× smaller FFT)

    Returns
    -------
    (dy, dx) : float tuple — shift in full-resolution pixels
    """
    ds = int(downsample)
    frm_ds = xp.asarray(frame_f32[::ds, ::ds], dtype=xp.float64)

    if _ref_fft is not None:
        # Fast path: reference FFT precomputed and cached by the caller.
        # Peak location is scale-invariant so frame normalisation is skipped.
        cross = _ref_fft * xp.conj(xp.fft.rfft2(frm_ds))
    else:
        ref_ds = xp.asarray(reference[::ds, ::ds], dtype=xp.float64)
        scale = max(float(ref_ds.max()), float(frm_ds.max()), 1.0)
        cross = xp.fft.rfft2(ref_ds / scale) * xp.conj(xp.fft.rfft2(frm_ds / scale))
    # Single host transfer of the (small, downsampled) correlation map, then peak
    # + sub-pixel on the CPU. This is intentionally ONE sync: splitting it into a
    # GPU argmax + per-slice transfers adds sync latency and is slower. The FFTs
    # dominate this function; a truly sync-free drift estimate belongs in the
    # fused pipeline kernel (see IMPLEMENTATION.md M3).
    corr = to_cpu(xp.fft.irfft2(cross))

    h, w = corr.shape
    flat_idx = int(np.argmax(corr))
    py, px = flat_idx // w, flat_idx % w

    def _sub_pixel(row, peak, n):
        """Quadratic interpolation around *peak* in *row*."""
        if peak == 0 or peak == n - 1:
            return float(peak)
        a, b, c = float(row[peak - 1]), float(row[peak]), float(row[peak + 1])
        denom = 2.0 * (2.0 * b - a - c)
        return float(peak) + (a - c) / denom if abs(denom) >= 1e-12 else float(peak)

    py_sub = _sub_pixel(corr[:, px], py, h)
    px_sub = _sub_pixel(corr[py, :], px, w)

    # Unwrap negative shifts
    if py_sub > h / 2:
        py_sub -= h
    if px_sub > w / 2:
        px_sub -= w

    max_shift = min(frame_f32.shape[0], frame_f32.shape[1]) // 4
    return (
        float(np.clip(py_sub * ds, -max_shift, max_shift)),
        float(np.clip(px_sub * ds, -max_shift, max_shift)),
    )
