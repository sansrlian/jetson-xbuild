cmake_minimum_required(VERSION 3.25)
project(jxb_trt_smoke LANGUAGES CXX)

find_package(CUDAToolkit REQUIRED)
find_path(TensorRT_INCLUDE_DIR NvInfer.h REQUIRED)
find_library(TensorRT_nvinfer nvinfer REQUIRED)
find_library(TensorRT_nvonnxparser nvonnxparser REQUIRED)
message(STATUS "TensorRT: ${TensorRT_INCLUDE_DIR} ${TensorRT_nvinfer}")

add_executable(trt_smoke main.cpp)
target_compile_features(trt_smoke PRIVATE cxx_std_17)
target_include_directories(trt_smoke PRIVATE ${TensorRT_INCLUDE_DIR})
target_link_libraries(trt_smoke PRIVATE ${TensorRT_nvinfer} ${TensorRT_nvonnxparser} CUDA::cudart)
install(TARGETS trt_smoke RUNTIME DESTINATION bin)
