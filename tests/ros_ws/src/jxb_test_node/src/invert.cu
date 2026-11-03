#include <cuda_runtime.h>
#include <cstdint>

__global__ void invert_kernel(uint8_t* data, int n) {
  int i = blockIdx.x * blockDim.x + threadIdx.x;
  if (i < n) data[i] = 255 - data[i];
}

// Inverts an 8-bit buffer on the GPU (stand-in for SAM's CUDA preprocessing).
cudaError_t invert_on_gpu(uint8_t* host, int n) {
  uint8_t* dev = nullptr;
  cudaError_t err = cudaMalloc(&dev, n);
  if (err != cudaSuccess) return err;
  cudaMemcpy(dev, host, n, cudaMemcpyHostToDevice);
  invert_kernel<<<(n + 255) / 256, 256>>>(dev, n);
  err = cudaMemcpy(host, dev, n, cudaMemcpyDeviceToHost);
  cudaFree(dev);
  return err;
}
