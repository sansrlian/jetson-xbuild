cmake_minimum_required(VERSION 3.25)
project(jxb_cuda_hello LANGUAGES CXX CUDA)

find_package(CUDAToolkit REQUIRED)

# Separate compilation (RDC) exercises nvlink and libcudadevrt from the sysroot.
add_library(jxb_kernels STATIC kernels.cu)
set_target_properties(jxb_kernels PROPERTIES CUDA_SEPARABLE_COMPILATION ON POSITION_INDEPENDENT_CODE ON)

add_executable(cuda_hello main.cu)
set_target_properties(cuda_hello PROPERTIES CUDA_RESOLVE_DEVICE_SYMBOLS ON)
target_link_libraries(cuda_hello PRIVATE jxb_kernels CUDA::cudart CUDA::cublas CUDA::nvtx3)
install(TARGETS cuda_hello RUNTIME DESTINATION bin)
