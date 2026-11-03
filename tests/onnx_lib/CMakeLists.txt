cmake_minimum_required(VERSION 3.25)
project(jxb_onnx_lib LANGUAGES C CXX)

# Builds the ONNX C++ library from source: host protoc + Python generate code, target libprotobuf links it.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(ONNX_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(ONNX_BUILD_PYTHON OFF CACHE BOOL "" FORCE)
set(ONNX_USE_PROTOBUF_SHARED_LIBS ON CACHE BOOL "" FORCE)
set(ONNX_ML ON CACHE BOOL "" FORCE)

include(FetchContent)
FetchContent_Declare(onnx
  URL https://github.com/onnx/onnx/archive/refs/tags/v1.17.0.tar.gz
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_MakeAvailable(onnx)

add_executable(onnx_check main.cpp)
target_link_libraries(onnx_check PRIVATE onnx onnx_proto)
install(TARGETS onnx_check RUNTIME DESTINATION bin)
