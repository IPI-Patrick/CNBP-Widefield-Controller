// fastacq.cu — fully GIL-free C++/CUDA acquisition + processing engine.
//
// The whole capture->process loop runs in a C++ std::thread that NEVER touches
// the Python GIL, so the Dear PyGui renderer cannot interfere with it. Python
// only: configures settings, starts/stops, and polls the latest RGBA frame +
// ROI means + FPS counters for display (at display rate).
//
// Frame sources:
//   * "mock"  — frames generated on the GPU by a CUDA kernel (self-contained;
//               no Python in the loop at all — this is the prototype-equivalent
//               benchmark path).
//   * "push"  — the real camera path: Python's Andor capture thread calls
//               submit(raw_uint16); the worker consumes from a double buffer.
//
// Pipeline (all on GPU, in the worker thread):
//   raw u16 -> integer drift shift (phase-correlation, cuFFT, phase_every)
//           -> LP IIR -> temporal-EMA background -> difference/contrast
//           -> crop mask -> ROI means -> colormap (LUT) -> RGBA float32
//
// Output: latest RGBA float32 (H*W*4, matches the Dear PyGui raw texture) is
// published double-buffered into pinned host memory; get_latest_rgba() copies
// the newest one. ROI means + capture/processing FPS are published atomically.
//
// Build: src/APIs/fastacq/build.bat (nvcc + cudart + cufft + MSVC).

#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>
#include <cuda_runtime.h>
#include <cufft.h>
#include <thread>
#include <atomic>
#include <mutex>
#include <condition_variable>
#include <vector>
#include <chrono>
#include <deque>
#include <stdexcept>
#include <cstring>
#include <cmath>

namespace py = pybind11;

#define CUDA_CHECK(call) do { cudaError_t _e=(call); if(_e!=cudaSuccess) \
    throw std::runtime_error(std::string("CUDA: ")+cudaGetErrorString(_e)); } while(0)
#define CUFFT_CHECK(call) do { cufftResult _r=(call); if(_r!=CUFFT_SUCCESS) \
    throw std::runtime_error("cuFFT error " + std::to_string((int)_r)); } while(0)

static const int TPB = 256;
static inline int nblocks(int n) { return (n + TPB - 1) / TPB; }

// ---------------- kernels ----------------

// Mock camera: drifting gaussian blob + structured background -> uint16.
__global__ void k_mock(unsigned short* frame, int H, int W, int fi,
                       float base_level, float signal, float sigma,
                       float drift_ax, float drift_ay) {
    int idx = blockIdx.x*blockDim.x + threadIdx.x; if (idx >= H*W) return;
    int x = idx % W, y = idx / W;
    float t = (float)fi;
    float cx = 0.5f*W + drift_ax*__sinf(t*0.033f);
    float cy = 0.5f*H + drift_ay*__cosf(t*0.025f);
    float dx = x-cx, dy = y-cy;
    float fx = (float)x/W, fy = (float)y/H;
    float bg = base_level + 420.f*__sinf(fx*40.f+t*0.018f) + 310.f*__cosf(fy*33.f-t*0.013f);
    float blob = signal*__expf(-(dx*dx+dy*dy)/(2.f*sigma*sigma));
    float v = bg + blob; v = fminf(fmaxf(v,0.f),65535.f);
    frame[idx] = (unsigned short)(v+0.5f);
}

// Fused: integer shift (wrap) + convert + LP IIR + EMA bg + diff/contrast.
__global__ void k_fused(const unsigned short* __restrict__ raw,
                        float* prev_in, float* prev_out, float* bg, float* __restrict__ out,
                        int H, int W, int sx, int sy, int lp, float b0,float b1,float a1,float maxv,
                        float alpha, int mode, int cy0,int cy1,int cx0,int cx1) {
    int idx = blockIdx.x*blockDim.x + threadIdx.x; if (idx >= H*W) return;
    int x = idx % W, y = idx / W;
    int gx = x - sx; gx %= W; if (gx<0) gx+=W;
    int gy = y - sy; gy %= H; if (gy<0) gy+=H;
    float s = (float)raw[gy*W+gx];
    float f;
    if (lp) { f = b0*s + b1*prev_in[idx] - a1*prev_out[idx]; f=fminf(fmaxf(f,0.f),maxv);
              prev_in[idx]=s; prev_out[idx]=f; } else f=s;
    float bgp=bg[idx]; float bgn=bgp+alpha*(f-bgp); bg[idx]=bgn;
    float fg=f-bgn;
    float r = (mode==2) ? (fg/(bgn+1.f)*100.f) : fg;
    if (y<cy0 || y>=cy1 || x<cx0 || x>=cx1) r = 0.f;   // crop mask
    out[idx] = r;
}

__global__ void k_seed(const unsigned short* raw, float* prev_in, float* prev_out, float* bg,
                       int H, int W, int sx, int sy) {
    int idx = blockIdx.x*blockDim.x + threadIdx.x; if (idx >= H*W) return;
    int x=idx%W, y=idx/W; int gx=x-sx; gx%=W; if(gx<0)gx+=W; int gy=y-sy; gy%=H; if(gy<0)gy+=H;
    float s=(float)raw[gy*W+gx]; prev_in[idx]=s; prev_out[idx]=s; bg[idx]=s;
}

__global__ void k_roi(const float* __restrict__ out, int H, int W,
                      const int* __restrict__ rects, int n, double* __restrict__ sums) {
    int idx = blockIdx.x*blockDim.x + threadIdx.x; if (idx >= H*W) return;
    int x=idx%W, y=idx/W; float v=out[idx];
    for (int r=0;r<n;++r){ int y0=rects[r*4],y1=rects[r*4+1],x0=rects[r*4+2],x1=rects[r*4+3];
        if (y>=y0&&y<y1&&x>=x0&&x<x1) atomicAdd(&sums[r],(double)v); }
}

// min/max reduction via float atomics (CAS trick)
__device__ float atomicMinF(float* a, float v){int* p=(int*)a;int o=*p,assumed;
    do{assumed=o;o=atomicCAS(p,assumed,__float_as_int(fminf(v,__int_as_float(assumed))));}while(assumed!=o);return __int_as_float(o);}
__device__ float atomicMaxF(float* a, float v){int* p=(int*)a;int o=*p,assumed;
    do{assumed=o;o=atomicCAS(p,assumed,__float_as_int(fmaxf(v,__int_as_float(assumed))));}while(assumed!=o);return __int_as_float(o);}
__global__ void k_minmax(const float* __restrict__ out, int N, float* mn, float* mx, int cy0,int cy1,int cx0,int cx1,int W){
    int idx=blockIdx.x*blockDim.x+threadIdx.x; if(idx>=N) return;
    int x=idx%W,y=idx/W; if(y<cy0||y>=cy1||x<cx0||x>=cx1) return;  // only valid (cropped) region
    float v=out[idx]; atomicMinF(mn,v); atomicMaxF(mx,v);
}

// colormap: normalize with [mn,mx] (or fixed) -> LUT -> RGBA float32
__global__ void k_color(const float* __restrict__ out, float* __restrict__ rgba,
                       const float* __restrict__ lut, int n_lut, int N,
                       float mn, float mx) {
    int idx=blockIdx.x*blockDim.x+threadIdx.x; if(idx>=N) return;
    float t=(out[idx]-mn)/(mx-mn); t=fminf(fmaxf(t,0.f),1.f);
    int li=(int)(t*(n_lut-1)+0.5f); if(li<0)li=0; if(li>=n_lut)li=n_lut-1;
    rgba[idx*4+0]=lut[li*3+0]; rgba[idx*4+1]=lut[li*3+1]; rgba[idx*4+2]=lut[li*3+2]; rgba[idx*4+3]=1.f;
}

// extract downsampled f32 view for drift (stride ds)
__global__ void k_downsample(const unsigned short* raw, float* dst, int H,int W,int ds,int hd,int wd){
    int idx=blockIdx.x*blockDim.x+threadIdx.x; if(idx>=hd*wd) return;
    int xd=idx%wd, yd=idx/wd; dst[idx]=(float)raw[(yd*ds)*W+(xd*ds)];
}
// cross-power: cur_fft * conj(ref_fft), normalized
__global__ void k_cross(cufftComplex* cur, const cufftComplex* ref, int M){
    int i=blockIdx.x*blockDim.x+threadIdx.x; if(i>=M) return;
    cufftComplex a=cur[i], b=ref[i];
    cufftComplex c; c.x=a.x*b.x+a.y*b.y; c.y=a.y*b.x-a.x*b.y;
    float m=sqrtf(c.x*c.x+c.y*c.y); if(m<1e-6f)m=1e-6f; c.x/=m; c.y/=m; cur[i]=c;
}

// ---------------- engine ----------------

struct Config {
    bool drift=false; int phase_every=1; int drift_ds=4;
    bool lp=false; float b0=0,b1=0,a1=0,maxv=65535.f;
    float alpha=0.02f; int mode=0; float crop=100.f;
    bool autoscale=true; float scale_min=0.f, scale_max=4096.f, grace=5.f;
    int output_stride=1;   // produce display RGBA + ROI means every N processed frames
    // mock params
    float base=12000.f, signal=18000.f, sigma=52.f, drift_ax=18.f, drift_ay=14.f;
    double target_fps=0.0;  // 0 = unpaced (max throughput)
};

class AcquisitionEngine {
public:
    AcquisitionEngine(int h,int w):H(h),W(w){
        N=(size_t)H*W;
        CUDA_CHECK(cudaMalloc(&d_raw,N*sizeof(unsigned short)));
        CUDA_CHECK(cudaMalloc(&d_prev_in,N*4)); CUDA_CHECK(cudaMalloc(&d_prev_out,N*4));
        CUDA_CHECK(cudaMalloc(&d_bg,N*4)); CUDA_CHECK(cudaMalloc(&d_out,N*4));
        CUDA_CHECK(cudaMalloc(&d_rgba,N*4*sizeof(float)));
        CUDA_CHECK(cudaMalloc(&d_mn,4)); CUDA_CHECK(cudaMalloc(&d_mx,4));
        CUDA_CHECK(cudaMalloc(&d_rects,maxRois*4*sizeof(int)));
        CUDA_CHECK(cudaMalloc(&d_sums,maxRois*sizeof(double)));
        CUDA_CHECK(cudaMallocHost(&h_push,N*sizeof(unsigned short)));
        for(int i=0;i<2;++i) CUDA_CHECK(cudaMallocHost(&h_rgba[i],N*4*sizeof(float)));
        CUDA_CHECK(cudaStreamCreateWithFlags(&stream,cudaStreamNonBlocking));
        // default grayscale LUT
        std::vector<float> g(256*3); for(int i=0;i<256;++i){float v=i/255.f;g[i*3]=v;g[i*3+1]=v;g[i*3+2]=v;}
        set_lut_vec(g,256);
    }
    ~AcquisitionEngine(){ stop();
        cudaFree(d_raw);cudaFree(d_prev_in);cudaFree(d_prev_out);cudaFree(d_bg);cudaFree(d_out);
        cudaFree(d_rgba);cudaFree(d_mn);cudaFree(d_mx);cudaFree(d_rects);cudaFree(d_sums);
        cudaFreeHost(h_push); for(int i=0;i<2;++i) cudaFreeHost(h_rgba[i]);
        if(d_lut)cudaFree(d_lut); free_drift(); cudaStreamDestroy(stream); }

    // All setters only STAGE data + set dirty flags (mutex-protected). The
    // worker thread performs every CUDA/cuFFT operation on its own stream, so
    // Python can reconfigure live with no cross-thread CUDA race.
    void configure(const Config& c){ std::lock_guard<std::mutex> lk(cfg_mtx); cfg=c; drift_dirty=true; }
    void set_lut(py::array_t<float,py::array::c_style|py::array::forcecast> lut){
        std::lock_guard<std::mutex> lk(cfg_mtx); stage_lut.assign(lut.data(),lut.data()+lut.size());
        stage_nlut=(int)(lut.size()/3); lut_dirty=true; }
    void set_rois(py::array_t<int,py::array::c_style|py::array::forcecast> rects){
        std::lock_guard<std::mutex> lk(cfg_mtx); int n=(int)(rects.size()/4); if(n>maxRois)n=maxRois;
        const int* p=rects.data(); stage_rects.assign(p,p+n*4); stage_area.assign(n,0.0);
        for(int r=0;r<n;++r){ long a=(long)(p[r*4+1]-p[r*4+0])*(long)(p[r*4+3]-p[r*4+2]); stage_area[r]=a>0?(double)a:0.0; }
        stage_nrois=n; rois_dirty=true; }
    void set_zero(py::array_t<unsigned short,py::array::c_style|py::array::forcecast> zero){
        std::lock_guard<std::mutex> lk(cfg_mtx); zero_host.assign(zero.data(),zero.data()+zero.size());
        have_zero=true; ref_dirty=true; }

    void start(const std::string& source){
        if(running) return; src_mock = (source!="push");
        running=true; seeded=false; cap_times.clear(); proc_times.clear(); frame_index=0; have_push=false;
        worker=std::thread(&AcquisitionEngine::loop,this);
    }
    void stop(){ if(!running) return; running=false; { std::lock_guard<std::mutex> lk(push_mtx); push_cv.notify_all(); }
        if(worker.joinable()) worker.join(); }

    // real camera: copy a raw frame in (GIL released by caller during the memcpy)
    void submit(py::array_t<unsigned short,py::array::c_style|py::array::forcecast> raw){
        if((size_t)raw.size()!=N) throw std::runtime_error("submit size mismatch");
        const unsigned short* p=raw.data();
        { py::gil_scoped_release rel; std::unique_lock<std::mutex> lk(push_mtx);
          std::memcpy(h_push,p,N*sizeof(unsigned short)); have_push=true; push_cv.notify_one(); } }

    // copy latest RGBA into the provided float32 array (H*W*4). Returns frame index.
    long get_latest_rgba(py::array_t<float,py::array::c_style> out){
        if((size_t)out.size()!=N*4) throw std::runtime_error("rgba size mismatch");
        int fi=front.load(); std::memcpy(out.mutable_data(),h_rgba[fi],N*4*sizeof(float));
        return published_index.load(); }

    std::vector<double> get_roi_means(){ std::lock_guard<std::mutex> lk(out_mtx); return last_means; }
    double capture_fps(){ return fps_from(cap_times); }
    double processing_fps(){ return fps_from(proc_times); }
    long frames_done(){ return published_index.load(); }

private:
    int H,W; size_t N;
    unsigned short *d_raw=nullptr; float *d_prev_in=nullptr,*d_prev_out=nullptr,*d_bg=nullptr,*d_out=nullptr,*d_rgba=nullptr;
    float *d_mn=nullptr,*d_mx=nullptr,*d_lut=nullptr; int n_lut=0;
    int *d_rects=nullptr; double *d_sums=nullptr; int n_rois=0; static const int maxRois=16;
    unsigned short* h_push=nullptr; float* h_rgba[2]={nullptr,nullptr};
    cudaStream_t stream;
    std::thread worker; std::atomic<bool> running{false}; bool src_mock=true, seeded=false;
    std::atomic<int> front{0}; std::atomic<long> published_index{0}; long frame_index=0;
    Config cfg; std::mutex cfg_mtx;
    std::mutex push_mtx; std::condition_variable push_cv; bool have_push=false;
    std::mutex out_mtx; std::vector<double> last_means; std::vector<double> roi_area;
    std::deque<double> cap_times, proc_times;
    std::vector<unsigned short> zero_host; bool have_zero=false; bool prev_lp=false;
    // staging for live reconfigure (applied by the worker on its own stream)
    bool drift_dirty=false, ref_dirty=false, lut_dirty=false, rois_dirty=false;
    std::vector<float> stage_lut; int stage_nlut=0;
    std::vector<int> stage_rects; std::vector<double> stage_area; int stage_nrois=0;
    // drift / cufft
    int ds_=0,hd=0,wd=0,Mcplx=0; cufftHandle planR2C=0, planC2R=0; bool plans_ok=false;
    float* d_ds=nullptr; cufftComplex* d_curfft=nullptr; cufftComplex* d_reffft=nullptr; float* d_corr=nullptr;
    std::vector<float> h_corr; int last_sx=0,last_sy=0; long drift_count=0;

    void set_lut_vec(const std::vector<float>& v,int n){ if(d_lut)cudaFree(d_lut);
        cudaMalloc(&d_lut,v.size()*sizeof(float)); cudaMemcpy(d_lut,v.data(),v.size()*sizeof(float),cudaMemcpyHostToDevice); n_lut=n; }

    void free_drift(){ if(planR2C){cufftDestroy(planR2C);planR2C=0;} if(planC2R){cufftDestroy(planC2R);planC2R=0;}
        if(d_ds){cudaFree(d_ds);d_ds=nullptr;} if(d_curfft){cudaFree(d_curfft);d_curfft=nullptr;}
        if(d_reffft){cudaFree(d_reffft);d_reffft=nullptr;} if(d_corr){cudaFree(d_corr);d_corr=nullptr;} plans_ok=false; }

    // Called ONLY from the worker thread (owns the stream). Builds cuFFT plans
    // for downsample factor ds if not already built.
    void setup_drift(int ds){
        if(ds<=0) ds=4;
        if(plans_ok && ds==ds_) return;
        free_drift(); ds_=ds; hd=H/ds; wd=W/ds; Mcplx=hd*(wd/2+1);
        if(hd<4||wd<4){ plans_ok=false; return; }
        if(cufftPlan2d(&planR2C,hd,wd,CUFFT_R2C)!=CUFFT_SUCCESS){plans_ok=false;return;}
        if(cufftPlan2d(&planC2R,hd,wd,CUFFT_C2R)!=CUFFT_SUCCESS){plans_ok=false;return;}
        cufftSetStream(planR2C,stream); cufftSetStream(planC2R,stream);
        cudaMalloc(&d_ds,hd*wd*sizeof(float)); cudaMalloc(&d_curfft,Mcplx*sizeof(cufftComplex));
        cudaMalloc(&d_reffft,Mcplx*sizeof(cufftComplex)); cudaMalloc(&d_corr,hd*wd*sizeof(float));
        h_corr.resize(hd*wd); plans_ok=true;
    }
    void build_ref_fft(){   // worker thread only
        if(!plans_ok || !have_zero) return;
        // upload zero, downsample, rfft -> d_reffft
        unsigned short* d_zraw; cudaMalloc(&d_zraw,N*sizeof(unsigned short));
        cudaMemcpyAsync(d_zraw,zero_host.data(),N*sizeof(unsigned short),cudaMemcpyHostToDevice,stream);
        k_downsample<<<nblocks(hd*wd),TPB,0,stream>>>(d_zraw,d_ds,H,W,ds_,hd,wd);
        cufftExecR2C(planR2C,d_ds,d_reffft);
        cudaStreamSynchronize(stream); cudaFree(d_zraw);
    }
    void compute_drift(){
        // d_raw -> downsample -> rfft -> cross w/ ref -> irfft -> argmax (host)
        k_downsample<<<nblocks(hd*wd),TPB,0,stream>>>(d_raw,d_ds,H,W,ds_,hd,wd);
        cufftExecR2C(planR2C,d_ds,d_curfft);
        k_cross<<<nblocks(Mcplx),TPB,0,stream>>>(d_curfft,d_reffft,Mcplx);
        cufftExecC2R(planC2R,d_curfft,d_corr);
        cudaMemcpyAsync(h_corr.data(),d_corr,hd*wd*sizeof(float),cudaMemcpyDeviceToHost,stream);
        cudaStreamSynchronize(stream);
        int best=0; float bv=h_corr[0]; for(int i=1;i<hd*wd;++i){ if(h_corr[i]>bv){bv=h_corr[i];best=i;} }
        int py=best/wd, px=best%wd; if(py>hd/2)py-=hd; if(px>wd/2)px-=wd;
        int maxs = (H<W?H:W)/4;
        int sy=py*ds_, sx=px*ds_; if(sy>maxs)sy=maxs; if(sy<-maxs)sy=-maxs; if(sx>maxs)sx=maxs; if(sx<-maxs)sx=-maxs;
        last_sy=sy; last_sx=sx;
    }

    static double now_s(){ return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
    static double fps_from(std::deque<double>& d){ if(d.size()<2) return 0.0; double span=d.back()-d.front(); return span<=0?0.0:(d.size()-1)/span; }
    void push_time(std::deque<double>& d){ double t=now_s(); d.push_back(t); double c=t-2.0; while(!d.empty()&&d.front()<c)d.pop_front(); }

    void loop(){
        cudaStream_t s=stream; int mock_fi=0; double next_deadline=now_s();
        while(running){
            // ---- apply any staged reconfigure (all CUDA on THIS thread/stream) ----
            Config c;
            bool ap_lut=false, ap_rois=false, ap_drift=false, ap_ref=false;
            std::vector<float> lutv; std::vector<int> rectsv; std::vector<double> areav; int nlut=0,nroi=0;
            { std::lock_guard<std::mutex> lk(cfg_mtx); c=cfg;
              if(lut_dirty){ ap_lut=true; lutv=stage_lut; nlut=stage_nlut; lut_dirty=false; }
              if(rois_dirty){ ap_rois=true; rectsv=stage_rects; areav=stage_area; nroi=stage_nrois; rois_dirty=false; }
              if(drift_dirty){ ap_drift=true; drift_dirty=false; }
              if(ref_dirty){ ap_ref=true; ref_dirty=false; }
            }
            if(ap_lut && nlut>0) set_lut_vec(lutv,nlut);
            if(ap_rois){ n_rois=(nroi>maxRois)?maxRois:nroi; roi_area=areav;
                if(n_rois>0) cudaMemcpyAsync(d_rects,rectsv.data(),n_rois*4*sizeof(int),cudaMemcpyHostToDevice,s); }
            if(c.drift && (ap_drift || !plans_ok)) setup_drift(c.drift_ds);
            if((ap_ref || ap_drift) && plans_ok && have_zero) build_ref_fft();
            // ---- acquire frame into d_raw ----
            if(src_mock){
                if(c.target_fps>0){ double per=1.0/c.target_fps; double t=now_s();
                    if(t<next_deadline){ double r=next_deadline-t; if(r>0.001)std::this_thread::sleep_for(std::chrono::duration<double>(r-0.0005)); while(now_s()<next_deadline){} }
                    next_deadline+=per; if(next_deadline<now_s())next_deadline=now_s()+per; }
                k_mock<<<nblocks((int)N),TPB,0,s>>>(d_raw,H,W,mock_fi++,c.base,c.signal,c.sigma,c.drift_ax,c.drift_ay);
            } else {
                std::unique_lock<std::mutex> lk(push_mtx);
                if(!push_cv.wait_for(lk,std::chrono::milliseconds(100),[&]{return have_push||!running;})) continue;
                if(!running) break; have_push=false;
                cudaMemcpyAsync(d_raw,h_push,N*sizeof(unsigned short),cudaMemcpyHostToDevice,s); lk.unlock();
            }
            push_time(cap_times);

            // ---- drift (phase_every) ----
            int sx=last_sx, sy=last_sy;
            if(c.drift && plans_ok && have_zero){
                int pe=c.phase_every>0?c.phase_every:1;
                if(drift_count % pe == 0){ compute_drift(); }
                drift_count++; sx=last_sx; sy=last_sy;
            } else { sx=0; sy=0; }

            // ---- crop bounds ----
            int cy0=0,cy1=H,cx0=0,cx1=W;
            if(c.crop<100.f){ float fr=c.crop/100.f; int ch=(int)(H*fr+0.5f),cw=(int)(W*fr+0.5f);
                cy0=(H-ch)/2; cy1=cy0+ch; cx0=(W-cw)/2; cx1=cx0+cw; }

            // ---- seed LP/bg on first frame or lp toggle ----
            if(!seeded || c.lp!=prev_lp){ k_seed<<<nblocks((int)N),TPB,0,s>>>(d_raw,d_prev_in,d_prev_out,d_bg,H,W,sx,sy); seeded=true; prev_lp=c.lp; }

            // ---- fused science pipeline (EVERY frame: LP/EMA state is stateful) ----
            k_fused<<<nblocks((int)N),TPB,0,s>>>(d_raw,d_prev_in,d_prev_out,d_bg,d_out,H,W,sx,sy,
                c.lp?1:0,c.b0,c.b1,c.a1,c.maxv,c.alpha,c.mode,cy0,cy1,cx0,cx1);

            // The DISPLAY products (ROI means, autoscale, colormap, RGBA D2H) are
            // expensive (a 16 B/px D2H + reductions) and only needed at display
            // rate — produce them every output_stride frames, not every frame.
            int ostride = c.output_stride>0 ? c.output_stride : 1;
            bool do_out = (frame_index % ostride == 0);
            if(do_out){
                std::vector<double> means(n_rois,0.0);
                if(n_rois>0){ cudaMemsetAsync(d_sums,0,n_rois*sizeof(double),s);
                    k_roi<<<nblocks((int)N),TPB,0,s>>>(d_out,H,W,d_rects,n_rois,d_sums);
                    cudaMemcpyAsync(means.data(),d_sums,n_rois*sizeof(double),cudaMemcpyDeviceToHost,s); }
                float mn=c.scale_min, mx=c.scale_max;
                if(c.autoscale){ float inf=1e30f; cudaMemcpyAsync(d_mn,&inf,4,cudaMemcpyHostToDevice,s);
                    float ninf=-1e30f; cudaMemcpyAsync(d_mx,&ninf,4,cudaMemcpyHostToDevice,s);
                    k_minmax<<<nblocks((int)N),TPB,0,s>>>(d_out,(int)N,d_mn,d_mx,cy0,cy1,cx0,cx1,W);
                    cudaMemcpyAsync(&mn,d_mn,4,cudaMemcpyDeviceToHost,s); cudaMemcpyAsync(&mx,d_mx,4,cudaMemcpyDeviceToHost,s);
                    cudaStreamSynchronize(s); float pad=(mx-mn)*c.grace/100.f; mn-=pad; mx+=pad; if(mx<=mn)mx=mn+1.f; }
                k_color<<<nblocks((int)N),TPB,0,s>>>(d_out,d_rgba,d_lut,n_lut,(int)N,mn,mx);
                int back=1-front.load();
                cudaMemcpyAsync(h_rgba[back],d_rgba,N*4*sizeof(float),cudaMemcpyDeviceToHost,s);
                cudaStreamSynchronize(s);
                for(int r=0;r<n_rois && r<(int)roi_area.size();++r){ double a=roi_area[r]; means[r]=(a>0.0)?means[r]/a:0.0; }
                { std::lock_guard<std::mutex> lk(out_mtx); last_means=means; }
                front.store(back); published_index.store(frame_index+1);
            } else {
                // Pace the worker to the GPU (and get true processing timing) with
                // one lightweight sync; the queued work here is just mock+fused.
                cudaStreamSynchronize(s);
            }
            frame_index++;
            push_time(proc_times);
        }
    }
};

PYBIND11_MODULE(fastacq, m){
    py::class_<Config>(m,"Config")
        .def(py::init<>())
        .def_readwrite("drift",&Config::drift).def_readwrite("phase_every",&Config::phase_every)
        .def_readwrite("drift_ds",&Config::drift_ds)
        .def_readwrite("lp",&Config::lp).def_readwrite("b0",&Config::b0).def_readwrite("b1",&Config::b1)
        .def_readwrite("a1",&Config::a1).def_readwrite("maxv",&Config::maxv)
        .def_readwrite("alpha",&Config::alpha).def_readwrite("mode",&Config::mode).def_readwrite("crop",&Config::crop)
        .def_readwrite("autoscale",&Config::autoscale).def_readwrite("scale_min",&Config::scale_min)
        .def_readwrite("scale_max",&Config::scale_max).def_readwrite("grace",&Config::grace)
        .def_readwrite("output_stride",&Config::output_stride)
        .def_readwrite("base",&Config::base).def_readwrite("signal",&Config::signal).def_readwrite("sigma",&Config::sigma)
        .def_readwrite("drift_ax",&Config::drift_ax).def_readwrite("drift_ay",&Config::drift_ay)
        .def_readwrite("target_fps",&Config::target_fps);
    py::class_<AcquisitionEngine>(m,"AcquisitionEngine")
        .def(py::init<int,int>(),py::arg("height"),py::arg("width"))
        .def("configure",&AcquisitionEngine::configure)
        .def("set_lut",&AcquisitionEngine::set_lut)
        .def("set_rois",&AcquisitionEngine::set_rois)
        .def("set_zero",&AcquisitionEngine::set_zero)
        .def("start",&AcquisitionEngine::start,py::arg("source")="mock")
        .def("stop",&AcquisitionEngine::stop)
        .def("submit",&AcquisitionEngine::submit)
        .def("get_latest_rgba",&AcquisitionEngine::get_latest_rgba)
        .def("get_roi_means",&AcquisitionEngine::get_roi_means)
        .def("capture_fps",&AcquisitionEngine::capture_fps)
        .def("processing_fps",&AcquisitionEngine::processing_fps)
        .def("frames_done",&AcquisitionEngine::frames_done);
}
