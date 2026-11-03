// Cross-build smoke test: CUDA runtime, an RDC kernel library, cuBLAS and NVTX on the Orin GPU.
#include <cmath>
#include <cstdio>
#include <vector>

#include <cublas_v2.h>
#include <cuda_runtime.h>
#include <nvtx3/nvToolsExt.h>

#include "kernels.cuh"

#define CUDA_CHECK(x)                                                                  \
  do {                                                                                 \
    cudaError_t e_ = (x);                                                              \
    if (e_ != cudaSuccess) {                                                           \
      std::fprintf(stderr, "%s:%d %s: %s\n", __FILE__, __LINE__, #x, cudaGetErrorString(e_)); \
      return 1;                                                                        \
    }                                                                                  \
  } while (0)

int main() {
  int runtime = 0, driver = 0, count = 0;
  CUDA_CHECK(cudaRuntimeGetVersion(&runtime));
  CUDA_CHECK(cudaDriverGetVersion(&driver));
  std::printf("cuda runtime %d, driver %d\n", runtime, driver);
  CUDA_CHECK(cudaGetDeviceCount(&count));
  cudaDeviceProp prop{};
  CUDA_CHECK(cudaGetDeviceProperties(&prop, 0));
  std::printf("device 0 of %d: %s, sm_%d%d, %d SMs, %.1f GiB\n", count, prop.name, prop.major, prop.minor,
              prop.multiProcessorCount, prop.totalGlobalMem / 1073741824.0);

  nvtxRangePushA("axpy");
  const int n = 1 << 20;
  std::vector<float> x(n), y(n);
  for (int i = 0; i < n; ++i) {
    x[i] = static_cast<float>(i % 100);
    y[i] = 1.0f;
  }
  float *dx = nullptr, *dy = nullptr;
  CUDA_CHECK(cudaMalloc(&dx, n * sizeof(float)));
  CUDA_CHECK(cudaMalloc(&dy, n * sizeof(float)));
  CUDA_CHECK(cudaMemcpy(dx, x.data(), n * sizeof(float), cudaMemcpyHostToDevice));
  CUDA_CHECK(cudaMemcpy(dy, y.data(), n * sizeof(float), cudaMemcpyHostToDevice));
  CUDA_CHECK(launch_axpy(n, 2.0f, dx, dy, nullptr));
  CUDA_CHECK(cudaMemcpy(y.data(), dy, n * sizeof(float), cudaMemcpyDeviceToHost));
  nvtxRangePop();
  for (int i = 0; i < n; ++i) {
    if (y[i] != 2.0f * static_cast<float>(i % 100) + 1.0f) {
      std::fprintf(stderr, "axpy mismatch at %d: %f\n", i, y[i]);
      return 1;
    }
  }

  nvtxRangePushA("sgemm");
  const int m = 64;
  std::vector<float> a(m * m, 1.0f), b(m * m, 2.0f), c(m * m, 0.0f);
  float *da = nullptr, *db = nullptr, *dc = nullptr;
  CUDA_CHECK(cudaMalloc(&da, m * m * sizeof(float)));
  CUDA_CHECK(cudaMalloc(&db, m * m * sizeof(float)));
  CUDA_CHECK(cudaMalloc(&dc, m * m * sizeof(float)));
  CUDA_CHECK(cudaMemcpy(da, a.data(), m * m * sizeof(float), cudaMemcpyHostToDevice));
  CUDA_CHECK(cudaMemcpy(db, b.data(), m * m * sizeof(float), cudaMemcpyHostToDevice));
  cublasHandle_t handle;
  if (cublasCreate(&handle) != CUBLAS_STATUS_SUCCESS) {
    std::fprintf(stderr, "cublasCreate failed\n");
    return 1;
  }
  const float alpha = 1.0f, beta = 0.0f;
  cublasSgemm(handle, CUBLAS_OP_N, CUBLAS_OP_N, m, m, m, &alpha, da, m, db, m, &beta, dc, m);
  CUDA_CHECK(cudaMemcpy(c.data(), dc, m * m * sizeof(float), cudaMemcpyDeviceToHost));
  cublasDestroy(handle);
  nvtxRangePop();
  if (std::fabs(c[0] - 2.0f * m) > 1e-3f) {
    std::fprintf(stderr, "sgemm mismatch: %f\n", c[0]);
    return 1;
  }

  cudaFree(dx);
  cudaFree(dy);
  cudaFree(da);
  cudaFree(db);
  cudaFree(dc);
  std::printf("cuda_hello: OK\n");
  return 0;
}
