# NVIDIA Jetson ORIN_NANO cross toolchain (aarch64 / JetPack sysroot)
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)
set(CMAKE_CROSSCOMPILING TRUE)

set(CMAKE_C_COMPILER_WORKS 1)
set(CMAKE_CXX_COMPILER_WORKS 1)
set(CMAKE_TRY_COMPILE_TARGET_TYPE "STATIC_LIBRARY")

set(CMAKE_SYSROOT $ENV{ARM64_SYSROOT})
set(CMAKE_FIND_ROOT_PATH $ENV{ARM64_SYSROOT})
set(CMAKE_LIBRARY_ARCHITECTURE aarch64-linux-gnu)

set(CMAKE_C_COMPILER "/usr/bin/aarch64-linux-gnu-gcc")
set(CMAKE_CXX_COMPILER "/usr/bin/aarch64-linux-gnu-g++")

# Orin CPU tuning (Ampere iGPU + Cortex-A78AE complex). Override via ORIN_CFLAGS.
set(ARM_COMPILE_OPTION "-mcpu=cortex-a78ae -fstack-protector-strong -Wformat -Wformat-security -Werror=format-security")
if(DEFINED ENV{ORIN_CFLAGS})
  set(ARM_COMPILE_OPTION "$ENV{ORIN_CFLAGS}")
endif()
if(NOT CMAKE_CXX_FLAGS MATCHES "mcpu=")
  set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} ${ARM_COMPILE_OPTION}")
  set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${ARM_COMPILE_OPTION}")
endif()

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)

set(THREADS_PTHREAD_ARG "0" CACHE STRING "Result from TRY_RUN" FORCE)
set(CMAKE_THREAD_LIBS_INIT "-lpthread")
set(CMAKE_HAVE_THREADS_LIBRARY 1)
set(CMAKE_USE_PTHREADS_INIT 1)
set(THREADS_PREFER_PTHREAD_FLAG ON)
set(ENABLE_PRECOMPILED_HEADERS OFF)
set(CPACK_DEBIAN_PACKAGE_ARCHITECTURE arm64)

set(GIT_EXECUTABLE "/usr/bin/git" CACHE FILEPATH "")
set(CMAKE_MAKE_PROGRAM "/usr/bin/make" CACHE FILEPATH "")
set(PKG_CONFIG_EXECUTABLE "/usr/bin/pkg-config" CACHE FILEPATH "")

# --- NVIDIA AI stack discovery inside the Jetson sysroot ---
set(_JP_SYSROOT "$ENV{ARM64_SYSROOT}")
list(APPEND CMAKE_FIND_ROOT_PATH
  "${_JP_SYSROOT}/usr/local/cuda"
  "${_JP_SYSROOT}/usr/local/cuda/targets/aarch64-linux"
)
# Common JetPack library dirs for rpath-link during cross link
if(CMAKE_SYSROOT)
  set(_NV_LIB_GLOBS
    "${CMAKE_SYSROOT}/usr/lib/${CMAKE_LIBRARY_ARCHITECTURE}"
    "${CMAKE_SYSROOT}/usr/local/cuda/lib64"
    "${CMAKE_SYSROOT}/usr/local/cuda/targets/aarch64-linux/lib"
    "${CMAKE_SYSROOT}/usr/lib"
  )
  set(LIB_DIRECTORIES "")
  foreach(_d IN LISTS _NV_LIB_GLOBS)
    if(EXISTS "${_d}")
      file(GLOB _sos "${_d}/lib*.so*")
      foreach(_so IN LISTS _sos)
        get_filename_component(_dir "${_so}" DIRECTORY)
        list(APPEND LIB_DIRECTORIES "${_dir}")
      endforeach()
    endif()
  endforeach()
  list(REMOVE_DUPLICATES LIB_DIRECTORIES)
  foreach(LIB_DIR IN LISTS LIB_DIRECTORIES)
    set(flag "-Wl,-rpath-link,${LIB_DIR}")
    if(NOT CMAKE_EXE_LINKER_FLAGS MATCHES "(^| )${flag}( |$)")
      set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} ${flag}")
    endif()
    if(NOT CMAKE_SHARED_LINKER_FLAGS MATCHES "(^| )${flag}( |$)")
      set(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} ${flag}")
    endif()
  endforeach()
endif()

# Helpful cache hints (packages still find via sysroot)
set(CUDA_TOOLKIT_ROOT_DIR "${_JP_SYSROOT}/usr/local/cuda" CACHE PATH "")
set(TensorRT_ROOT "${_JP_SYSROOT}/usr" CACHE PATH "")
set(CUDNN_INCLUDE_DIR "${_JP_SYSROOT}/usr/include" CACHE PATH "")
