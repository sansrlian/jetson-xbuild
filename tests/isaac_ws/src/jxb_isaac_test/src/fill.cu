#include <cstddef>
#include <cstdint>
#include <cuda_runtime.h>

__global__ void fill_kernel(uint8_t* data, size_t n, uint8_t value) {
  size_t i = blockIdx.x * static_cast<size_t>(blockDim.x) + threadIdx.x;
  if (i < n) data[i] = static_cast<uint8_t>(value + (i % 7));
}

cudaError_t fill(uint8_t* data, size_t n, uint8_t value, cudaStream_t stream) {
  fill_kernel<<<(n + 255) / 256, 256, 0, stream>>>(data, n, value);
  return cudaGetLastError();
}
