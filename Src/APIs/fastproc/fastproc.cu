// fastproc.cu — GIL-free CUDA processing engine for the live camera pipeline.
//
// Why this exists:
//   At high frame rates the Python processing thread competes with the Dear
//   PyGui render loop for the GIL and gets starved (Processing FPS < Capture
//   FPS). This module runs the heavy per-frame work (H2D upload + the fused
//   science kernel + ROI means) inside ONE call that RELEASES the GIL
//   (py::gil_scoped_release), so the render loop can never starve it. The
//   per-frame Python footprint shrinks to a single function call.
//
// Scope (temporal/fast pipeline path, matches Utils/fused_kernels.py semantics):
//   raw uint16 -> integer drift shift -> convert f32 -> LP IIR -> temporal EMA
//   background -> difference/contrast. The processed frame stays on the GPU
//   (retrievable as a device pointer for the display colormap, which runs at
//   display rate in CuPy). ROI means are reduced on-device and returned.
//
//   Drift ESTIMATION (FFT) stays in Python/CuPy (cadenced by phase_every); the
//   integer shift it produces is passed in here and applied during the gather.
//
// Built via nvcc + MSVC into fastproc<abi>.pyd (see build.bat). Uses the device
// PRIMARY context (same as CuPy), so the returned device pointer interoperates
// with CuPy arrays.

#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>
#include <cuda_runtime.h>
#include <vector>
#include <stdexcept>
#include <cstring>

namespace py = pybind11;

#define CUDA_CHECK(call)                                                        \
    do {                                                                        \
        cudaError_t _e = (call);                                                \
        if (_e != cudaSuccess) {                                                \
            throw std::runtime_error(std::string("CUDA error: ") +              \
                                     cudaGetErrorString(_e));                   \
        }                                                                       \
    } while (0)

// Fused: integer drift shift (wrap) + convert u16->f32 + LP IIR + EMA bg +
// difference/contrast. One pass; updates LP state and bg in place.
__global__ void fused_kernel(
    const unsigned short* __restrict__ raw,
    float* prev_in, float* prev_out, float* bg, float* __restrict__ out,
    int H, int W, int shift_x, int shift_y,
    int lp_enabled, float b0, float b1, float a1, float maxv,
    float alpha, int mode)
{
    int idx = blockDim.x * blockIdx.x + threadIdx.x;
    int total = H * W;
    if (idx >= total) return;
    int y = idx / W;
    int x = idx % W;

    // integer shift with wraparound (source pixel)
    int sx = x - shift_x; sx %= W; if (sx < 0) sx += W;
    int sy = y - shift_y; sy %= H; if (sy < 0) sy += H;
    float s = (float)raw[sy * W + sx];

    float f;
    if (lp_enabled != 0) {
        f = b0 * s + b1 * prev_in[idx] - a1 * prev_out[idx];
        f = fminf(fmaxf(f, 0.0f), maxv);
        prev_in[idx] = s;
        prev_out[idx] = f;
    } else {
        f = s;
    }
    float bg_prev = bg[idx];
    float bg_new = bg_prev + alpha * (f - bg_prev);
    bg[idx] = bg_new;
    float fg = f - bg_new;
    out[idx] = (mode == 2) ? (fg / (bg_new + 1.0f) * 100.0f) : fg;
}

// Seed kernel: fill prev_in, prev_out, bg with the (shifted) first frame value
// so the LP recurrence passes through on frame 1 and the EMA starts at frame 1.
__global__ void seed_kernel(const unsigned short* __restrict__ raw, float* prev_in,
                            float* prev_out, float* bg,
                            int H, int W, int shift_x, int shift_y) {
    int idx = blockDim.x * blockIdx.x + threadIdx.x;
    if (idx >= H * W) return;
    int y = idx / W, x = idx % W;
    int sx = x - shift_x; sx %= W; if (sx < 0) sx += W;
    int sy = y - shift_y; sy %= H; if (sy < 0) sy += H;
    float s = (float)raw[sy * W + sx];
    prev_in[idx] = s; prev_out[idx] = s; bg[idx] = s;
}

// ROI sums: each thread adds its pixel into every ROI whose rectangle contains
// it (few ROIs, so the per-pixel loop is cheap). roi_rects is N*4 (y0,y1,x0,x1).
__global__ void roi_sum_kernel(
    const float* __restrict__ out, int H, int W,
    const int* __restrict__ rects, int n_rois, double* __restrict__ sums)
{
    int idx = blockDim.x * blockIdx.x + threadIdx.x;
    if (idx >= H * W) return;
    int y = idx / W, x = idx % W;
    float v = out[idx];
    for (int r = 0; r < n_rois; ++r) {
        int y0 = rects[r*4+0], y1 = rects[r*4+1], x0 = rects[r*4+2], x1 = rects[r*4+3];
        if (y >= y0 && y < y1 && x >= x0 && x < x1) {
            atomicAdd(&sums[r], (double)v);
        }
    }
}

class Engine {
public:
    Engine(int height, int width) : H(height), W(width) {
        total = (size_t)H * W;
        CUDA_CHECK(cudaStreamCreateWithFlags(&stream, cudaStreamNonBlocking));
        CUDA_CHECK(cudaMalloc(&d_raw,      total * sizeof(unsigned short)));
        CUDA_CHECK(cudaMalloc(&d_prev_in,  total * sizeof(float)));
        CUDA_CHECK(cudaMalloc(&d_prev_out, total * sizeof(float)));
        CUDA_CHECK(cudaMalloc(&d_bg,       total * sizeof(float)));
        CUDA_CHECK(cudaMalloc(&d_out,      total * sizeof(float)));
        CUDA_CHECK(cudaMallocHost(&h_pinned, total * sizeof(unsigned short)));
        CUDA_CHECK(cudaMemset(d_prev_in,  0, total * sizeof(float)));
        CUDA_CHECK(cudaMemset(d_prev_out, 0, total * sizeof(float)));
        CUDA_CHECK(cudaMemset(d_bg,       0, total * sizeof(float)));
        seeded = false;
        max_rois = 16;
        CUDA_CHECK(cudaMalloc(&d_rects, max_rois * 4 * sizeof(int)));
        CUDA_CHECK(cudaMalloc(&d_sums,  max_rois * sizeof(double)));
    }
    ~Engine() {
        cudaFree(d_raw); cudaFree(d_prev_in); cudaFree(d_prev_out);
        cudaFree(d_bg); cudaFree(d_out); cudaFreeHost(h_pinned);
        cudaFree(d_rects); cudaFree(d_sums); cudaStreamDestroy(stream);
    }

    // Process one raw uint16 frame. Returns ROI means (length n_rois).
    // GIL is released for the entire GPU operation.
    std::vector<double> process(
        py::array_t<unsigned short, py::array::c_style | py::array::forcecast> raw,
        int shift_x, int shift_y,
        bool lp_enabled, float b0, float b1, float a1, float maxv,
        float alpha, int mode,
        py::array_t<int, py::array::c_style | py::array::forcecast> roi_rects)
    {
        if ((size_t)raw.size() != total)
            throw std::runtime_error("frame size mismatch");
        int n_rois = (int)(roi_rects.size() / 4);
        if (n_rois > max_rois) n_rois = max_rois;

        // Copy frame into pinned host buffer (cheap; before releasing GIL we
        // grab the pointers, then do all CUDA work GIL-free).
        const unsigned short* src = raw.data();
        const int* rects = (n_rois > 0) ? roi_rects.data() : nullptr;
        std::vector<double> means(n_rois, 0.0);

        {
            py::gil_scoped_release release;  // <-- GIL released for all GPU work
            std::memcpy(h_pinned, src, total * sizeof(unsigned short));
            CUDA_CHECK(cudaMemcpyAsync(d_raw, h_pinned,
                       total * sizeof(unsigned short), cudaMemcpyHostToDevice, stream));

            // First frame: seed LP state + EMA bg from this frame so the IIR
            // (normalized to pass-through when prev==input) starts cleanly.
            if (!seeded || lp_enabled != prev_lp_enabled) {
                // run a no-LP convert+shift to fill d_prev_*/d_bg with frame
                seed_kernel<<<blocks(), 256, 0, stream>>>(d_raw, d_prev_in, d_prev_out, d_bg,
                                                          H, W, shift_x, shift_y);
                seeded = true;
                prev_lp_enabled = lp_enabled;
            }

            fused_kernel<<<blocks(), 256, 0, stream>>>(
                d_raw, d_prev_in, d_prev_out, d_bg, d_out,
                H, W, shift_x, shift_y, lp_enabled ? 1 : 0,
                b0, b1, a1, maxv, alpha, mode);

            if (n_rois > 0) {
                CUDA_CHECK(cudaMemcpyAsync(d_rects, rects, n_rois * 4 * sizeof(int),
                           cudaMemcpyHostToDevice, stream));
                CUDA_CHECK(cudaMemsetAsync(d_sums, 0, n_rois * sizeof(double), stream));
                roi_sum_kernel<<<blocks(), 256, 0, stream>>>(
                    d_out, H, W, d_rects, n_rois, d_sums);
                CUDA_CHECK(cudaMemcpyAsync(means.data(), d_sums, n_rois * sizeof(double),
                           cudaMemcpyDeviceToHost, stream));
            }
            CUDA_CHECK(cudaStreamSynchronize(stream));
        }
        // divide ROI sums by area (host side, GIL held again — trivial)
        for (int r = 0; r < n_rois; ++r) {
            const int* rr = roi_rects.data() + r * 4;
            long area = (long)(rr[1] - rr[0]) * (long)(rr[3] - rr[2]);
            means[r] = (area > 0) ? means[r] / (double)area : 0.0;
        }
        return means;
    }

    // Device pointer to the processed f32 frame (H*W), for CuPy to wrap.
    uintptr_t out_ptr() const { return (uintptr_t)d_out; }
    int height() const { return H; }
    int width() const { return W; }

    // forward decl of seed kernel launcher uses this
    int blocks() const { return (int)((total + 255) / 256); }

private:
    int H, W; size_t total;
    cudaStream_t stream;
    unsigned short* d_raw = nullptr;
    float* d_prev_in = nullptr; float* d_prev_out = nullptr;
    float* d_bg = nullptr; float* d_out = nullptr;
    unsigned short* h_pinned = nullptr;
    int* d_rects = nullptr; double* d_sums = nullptr; int max_rois = 16;
    bool seeded = false; bool prev_lp_enabled = false;
};

PYBIND11_MODULE(fastproc, m) {
    m.doc() = "GIL-free CUDA temporal processing engine for the camera pipeline";
    py::class_<Engine>(m, "Engine")
        .def(py::init<int, int>(), py::arg("height"), py::arg("width"))
        .def("process", &Engine::process,
             py::arg("raw"), py::arg("shift_x"), py::arg("shift_y"),
             py::arg("lp_enabled"), py::arg("b0"), py::arg("b1"), py::arg("a1"),
             py::arg("max_value"), py::arg("alpha"), py::arg("mode"),
             py::arg("roi_rects"))
        .def("out_ptr", &Engine::out_ptr)
        .def("height", &Engine::height)
        .def("width", &Engine::width);
}
