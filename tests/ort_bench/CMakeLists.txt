cmake_minimum_required(VERSION 3.25)
project(jxb_ort_bench LANGUAGES CXX)

# Same ONNX Runtime discovery as ort_smoke / SAM's object_detection_package (board path /opt/onnxruntime).
set(ONNXRUNTIME_DIR /opt/onnxruntime)
add_library(onnxruntime SHARED IMPORTED)
set_target_properties(onnxruntime PROPERTIES
  IMPORTED_LOCATION ${ONNXRUNTIME_DIR}/lib/libonnxruntime.so
  INTERFACE_INCLUDE_DIRECTORIES ${ONNXRUNTIME_DIR}/include)

add_executable(ort_bench main.cpp)
target_compile_features(ort_bench PRIVATE cxx_std_17)
target_link_libraries(ort_bench PRIVATE onnxruntime)
install(TARGETS ort_bench RUNTIME DESTINATION bin)
