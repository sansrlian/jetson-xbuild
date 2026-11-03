#pragma once
#include <cuda_runtime.h>

cudaError_t launch_axpy(int n, float a, const float* x, float* y, cudaStream_t stream);
