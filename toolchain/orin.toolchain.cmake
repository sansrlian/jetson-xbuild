# jxb CMake toolchain: x86_64 build host -> NVIDIA Jetson Orin (aarch64), against a JetPack sysroot.
#
# Board-specific values come from the environment that toolchain/env.sh derives from the profile
# (cross-colcon-build sources it; plain CMake users: `source /opt/jxb/toolchain/env.sh`).
# C/C++ use the aarch64 GCC with --sysroot; CUDA uses the x86_64 nvcc, whose targets/<cuda_target> directory is
# bind-mounted from the sysroot, so device and host code are built against the board's exact CUDA.
foreach(_jxb_var ARM64_SYSROOT ROS_DISTRO JXB_CPU_FLAGS JXB_CUDA_VERSION JXB_CUDA_ARCH JXB_CUDA_TARGET JXB_PYTHON_VERSION)
  if(NOT DEFINED ENV{${_jxb_var}})
    message(FATAL_ERROR "jxb toolchain: ${_jxb_var} is not set. Source /opt/jxb/toolchain/env.sh first.")
  endif()
endforeach()

set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)
set(CMAKE_LIBRARY_ARCHITECTURE aarch64-linux-gnu)

set(JXB_SYSROOT "$ENV{ARM64_SYSROOT}")
set(CMAKE_SYSROOT "${JXB_SYSROOT}")

set(CMAKE_C_COMPILER aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER aarch64-linux-gnu-g++)
set(CMAKE_C_FLAGS_INIT "$ENV{JXB_CPU_FLAGS}")
set(CMAKE_CXX_FLAGS_INIT "$ENV{JXB_CPU_FLAGS}")

find_program(JXB_CCACHE ccache)
if(JXB_CCACHE)
  foreach(_lang C CXX CUDA)
    if(NOT DEFINED CMAKE_${_lang}_COMPILER_LAUNCHER AND NOT DEFINED ENV{CMAKE_${_lang}_COMPILER_LAUNCHER})
      set(CMAKE_${_lang}_COMPILER_LAUNCHER "${JXB_CCACHE}")
    endif()
  endforeach()
endif()

# Search target files only in the sysroot. Packages built in the same colcon workspace live outside of it;
# their install base is added as an extra root (CMake does not re-root paths that are already under a root).
# Append, never overwrite: toolchain files are read several times, and wrappers that chainload this file (vcpkg)
# add their own roots after the first read.
list(APPEND CMAKE_FIND_ROOT_PATH "${JXB_SYSROOT}")
if(DEFINED ENV{JXB_WS_INSTALL})
  string(REPLACE ":" ";" _jxb_ws "$ENV{JXB_WS_INSTALL}")
  list(APPEND CMAKE_FIND_ROOT_PATH ${_jxb_ws})
endif()
list(REMOVE_DUPLICATES CMAKE_FIND_ROOT_PATH)
# Merged /usr: a package config found via the lib -> usr/lib symlink (e.g. when "/" is a prefix, as vcpkg adds it)
# would compute its install prefix as the sysroot root instead of <sysroot>/usr. Resolve symlinks first.
set(CMAKE_FIND_PACKAGE_RESOLVE_SYMLINKS ON)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)

# Let the linker resolve transitive dependencies of sysroot libraries: the board's ld.so.conf.d entries plus the
# directories that are only on the board's LD_LIBRARY_PATH (ROS, ONNX Runtime).
set(_jxb_libdirs
  /lib/aarch64-linux-gnu /usr/lib/aarch64-linux-gnu
  /opt/ros/$ENV{ROS_DISTRO}/lib /opt/ros/$ENV{ROS_DISTRO}/lib/aarch64-linux-gnu
  /usr/local/cuda-$ENV{JXB_CUDA_VERSION}/targets/$ENV{JXB_CUDA_TARGET}/lib
  /opt/onnxruntime/lib /opt/acados/lib)
file(GLOB _jxb_ldconf "${JXB_SYSROOT}/etc/ld.so.conf.d/*.conf")
foreach(_jxb_conf IN LISTS _jxb_ldconf)
  file(STRINGS "${_jxb_conf}" _jxb_lines REGEX "^/")
  list(APPEND _jxb_libdirs ${_jxb_lines})
endforeach()
set(_jxb_link "--sysroot=${JXB_SYSROOT}")
foreach(_jxb_dir IN LISTS _jxb_libdirs)
  string(STRIP "${_jxb_dir}" _jxb_dir)
  if(IS_DIRECTORY "${JXB_SYSROOT}${_jxb_dir}")
    string(APPEND _jxb_link " -Wl,-rpath-link,${JXB_SYSROOT}${_jxb_dir}")
  endif()
endforeach()
# --sysroot is repeated here because CMake passes no sysroot flag when the host compiler links CUDA targets.
set(CMAKE_EXE_LINKER_FLAGS_INIT "${_jxb_link}")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "${_jxb_link}")
set(CMAKE_MODULE_LINKER_FLAGS_INIT "${_jxb_link}")

# pkg-config: only the sysroot's .pc files, with their paths re-rooted.
set(ENV{PKG_CONFIG_DIR} "")
set(ENV{PKG_CONFIG_PATH} "")
set(ENV{PKG_CONFIG_LIBDIR}
  "${JXB_SYSROOT}/usr/lib/aarch64-linux-gnu/pkgconfig:${JXB_SYSROOT}/usr/lib/pkgconfig:${JXB_SYSROOT}/usr/share/pkgconfig:${JXB_SYSROOT}/opt/ros/$ENV{ROS_DISTRO}/lib/pkgconfig")
set(ENV{PKG_CONFIG_SYSROOT_DIR} "${JXB_SYSROOT}")

# Python: the host interpreter runs code generators (rosidl, ament); headers, libpython and the extension suffix
# are the board's. Without the SOABI override, extension modules would get an x86_64 suffix and not import.
set(_jxb_py "$ENV{JXB_PYTHON_VERSION}")
string(REPLACE "." "" _jxb_pynodot "${_jxb_py}")
set(_jxb_numpy "${JXB_SYSROOT}/usr/lib/python3/dist-packages/numpy/_core/include")   # numpy >= 2
if(NOT IS_DIRECTORY "${_jxb_numpy}")
  set(_jxb_numpy "${JXB_SYSROOT}/usr/lib/python3/dist-packages/numpy/core/include")
endif()
foreach(_jxb_p Python3 Python)
  set(${_jxb_p}_EXECUTABLE /usr/bin/python3 CACHE FILEPATH "Host Python for code generators")
  set(${_jxb_p}_INCLUDE_DIR "${JXB_SYSROOT}/usr/include/python${_jxb_py}" CACHE PATH "")
  set(${_jxb_p}_LIBRARY "${JXB_SYSROOT}/usr/lib/aarch64-linux-gnu/libpython${_jxb_py}.so" CACHE FILEPATH "")
  set(${_jxb_p}_NumPy_INCLUDE_DIR "${_jxb_numpy}" CACHE PATH "")
  set(${_jxb_p}_SOABI "cpython-${_jxb_pynodot}-aarch64-linux-gnu" CACHE STRING "")
endforeach()
set(PYTHON_MODULE_EXTENSION ".cpython-${_jxb_pynodot}-aarch64-linux-gnu.so" CACHE STRING "")   # pybind11
set(PythonExtra_EXTENSION_SUFFIX ".cpython-${_jxb_pynodot}-aarch64-linux-gnu" CACHE STRING "") # python_cmake_module

# CUDA: x86_64 nvcc, aarch64 host compiler, Orin GPU architecture.
set(JXB_CUDA_ROOT "/usr/local/cuda-$ENV{JXB_CUDA_VERSION}")
set(CMAKE_CUDA_COMPILER "${JXB_CUDA_ROOT}/bin/nvcc")
set(CMAKE_CUDA_HOST_COMPILER aarch64-linux-gnu-g++)
if(NOT DEFINED CMAKE_CUDA_ARCHITECTURES)
  set(CMAKE_CUDA_ARCHITECTURES "$ENV{JXB_CUDA_ARCH}")
endif()
string(REPLACE " " "," _jxb_cpu_xc "$ENV{JXB_CPU_FLAGS}")
set(CMAKE_CUDA_FLAGS_INIT
  "--target-directory $ENV{JXB_CUDA_TARGET} -Xcompiler=--sysroot=${JXB_SYSROOT},${_jxb_cpu_xc}")
set(CUDAToolkit_ROOT "${JXB_CUDA_ROOT}")
# Legacy FindCUDA (still used e.g. by isaac_ros_common): it takes the cross target dir only from the environment
# variable CUDA_TOOLKIT_ROOT (<root>/targets/aarch64-linux, the bind-mounted board tree).
set(CUDA_TOOLKIT_ROOT_DIR "${JXB_CUDA_ROOT}")
set(ENV{CUDA_TOOLKIT_ROOT} "${JXB_CUDA_ROOT}")

# Board install layout (same as the SAM dev image): ONNX Runtime in /opt/onnxruntime.
set(onnxruntime_ROOT "${JXB_SYSROOT}/opt/onnxruntime")
