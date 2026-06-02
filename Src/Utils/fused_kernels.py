"""Fused CUDA kernels for the high-throughput (temporal-BG) pipeline path.

These collapse the per-pixel science chain (LP filter -> temporal-EMA background
removal -> difference/contrast -> crop) into a SINGLE alloc-free kernel writing
into pre-allocated, persistent device buffers. This is the prototype's
``POSTPROCESS_KERNEL`` idea adapted to our science and is the path that reaches
>1000 fps with all features, because it removes the spatial ``uniform_filter``
(~0.67 ms) entirely and replaces ~5 CuPy ops + their intermediate allocations
with one kernel launch.

Used only when ``ProcessingSettings.bg_mode == "temporal"``. The exact spatial
path (``uniform_filter``) is unchanged and remains the default.

Compiled lazily via NVRTC (cupy RawModule); no nvcc/MSVC build step required.
On CPU-only systems this module is never used (callers branch on GPU_AVAILABLE).
"""

from Utils.gpu import xp, GPU_AVAILABLE

# display_mode -> integer code passed to the kernel
MODE_NORMAL = 0
MODE_DIFFERENCE = 1
MODE_CONTRAST = 2

_MODULE = None
_FN = None

_SOURCE = r'''
extern "C" __global__
void lp_ema_postprocess(
    const float* __restrict__ in,   // shifted (drift-corrected) float32 frame
    float* prev_in,                 // LP filter state: previous input  (persistent)
    float* prev_out,                // LP filter state: previous output (persistent)
    float* bg,                      // temporal EMA background          (persistent)
    float* __restrict__ out,        // result (foreground / contrast)
    const int H, const int W,
    const int lp_enabled,
    const float b0, const float b1, const float a1, const float maxv,
    const float alpha,              // EMA rate; bg += alpha*(f - bg)
    const int mode)                 // 0 Normal, 1 Difference, 2 Contrast
{
    const int idx = blockDim.x * blockIdx.x + threadIdx.x;
    const int total = H * W;
    if (idx >= total) return;

    const float s = in[idx];

    // --- Low-pass IIR (matches the eager pipeline's recurrence) ---
    float f;
    if (lp_enabled != 0) {
        f = b0 * s + b1 * prev_in[idx] - a1 * prev_out[idx];
        f = fminf(fmaxf(f, 0.0f), maxv);
        prev_in[idx]  = s;
        prev_out[idx] = f;
    } else {
        f = s;
    }

    // --- Temporal-EMA background + foreground ---
    const float bg_prev = bg[idx];
    const float bg_new  = bg_prev + alpha * (f - bg_prev);
    bg[idx] = bg_new;
    const float fg = f - bg_new;

    // --- Display mode (background-subtracted; Normal/Difference identical) ---
    if (mode == 2) {            // Contrast
        out[idx] = fg / (bg_new + 1.0f) * 100.0f;
    } else {                    // Normal / Difference
        out[idx] = fg;
    }
}
'''


def _get_fn():
    global _MODULE, _FN
    if _FN is None:
        if not GPU_AVAILABLE:
            return None
        _MODULE = xp.RawModule(code=_SOURCE)
        _FN = _MODULE.get_function("lp_ema_postprocess")
    return _FN


def lp_ema_postprocess(shifted, prev_in, prev_out, bg, out, *,
                       lp_enabled, b0, b1, a1, max_value, alpha, mode):
    """Run the fused temporal-BG postprocess kernel in place.

    All array args are device float32 of shape (H, W); prev_in/prev_out/bg/out
    are caller-owned persistent buffers (modified in place). Returns ``out``.
    Border-zeroing and crop are applied by the caller afterward (reuses the
    existing eager code), so they are not in the kernel.
    """
    import numpy as _np
    fn = _get_fn()
    H, W = shifted.shape
    total = H * W
    threads = 256
    blocks = (total + threads - 1) // threads
    fn((blocks,), (threads,), (
        shifted, prev_in, prev_out, bg, out,
        _np.int32(H), _np.int32(W),
        _np.int32(1 if lp_enabled else 0),
        _np.float32(b0), _np.float32(b1), _np.float32(a1), _np.float32(max_value),
        _np.float32(alpha), _np.int32(int(mode)),
    ))
    return out
