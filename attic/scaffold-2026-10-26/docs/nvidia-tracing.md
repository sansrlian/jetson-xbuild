# NVIDIA tracing & optimization

## On-board prerequisites
- Nsight Systems CLI (`nsys`) — JetPack / SDK Manager component
- Nsight Compute CLI (`ncu`) — optional, for kernel-level metrics
- `tegrastats`, `trace-cmd` (optional for kernel traces)

## From the cross-dev container
```bash
board select orin-agx-01
board nsys --duration 15 -- my_pkg my_node
# reports land in $ROS2_WS/nsight_reports/*.nsys-rep
```

Open reports on the workstation with Nsight Systems GUI (host install) or any
`nsight-systems` package present in the container.

## CUDA / TensorRT link
CMake toolchains add sysroot `usr/local/cuda` and JetPack lib dirs to
`rpath-link`. Your packages should use standard `find_package(CUDAToolkit)`,
TensorRT CMake modules, or pkg-config against the sysroot via
`cross-colcon-build` environment.
