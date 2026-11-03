#include "kernels.cuh"

__device__ float axpy_element(float a, float x, float y) { return a * x + y; }

__global__ void axpy_kernel(int n, float a, const float* x, float* y) {
  int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) y[i] = axpy_element(a, x[i], y[i]);
}

cudaError_t launch_axpy(int n, float a, const float* x, float* y, cudaStream_t stream) {
  const int block = 256;
  axpy_kernel<<<(n + block - 1) / block, block, 0, stream>>>(n, a, x, y);
  return cudaGetLastError();
}
