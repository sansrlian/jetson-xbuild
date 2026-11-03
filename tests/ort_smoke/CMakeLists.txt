cmake_minimum_required(VERSION 3.25)
project(jxb_ort_smoke LANGUAGES CXX)

# Same way SAM's object_detection_package finds ONNX Runtime: the board path /opt/onnxruntime
# (inside the jxb container it is mapped to the sysroot's copy).
set(ONNXRUNTIME_DIR /opt/onnxruntime)
add_library(onnxruntime SHARED IMPORTED)
set_target_properties(onnxruntime PROPERTIES
  IMPORTED_LOCATION ${ONNXRUNTIME_DIR}/lib/libonnxruntime.so
  INTERFACE_INCLUDE_DIRECTORIES ${ONNXRUNTIME_DIR}/include)

add_executable(ort_smoke main.cpp)
target_compile_features(ort_smoke PRIVATE cxx_std_17)
target_link_libraries(ort_smoke PRIVATE onnxruntime)
install(TARGETS ort_smoke RUNTIME DESTINATION bin)
