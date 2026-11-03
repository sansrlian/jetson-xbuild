#!/usr/bin/env bash
# Cross-builds ONNX Runtime with the CUDA and TensorRT execution providers for the Orin GPU (sm_87) and packs it
# in the layout of the official release tarball (include/, lib/), so it can replace /opt/onnxruntime.
# The released aarch64 CUDA EP has no sm_87 code (docs/findings.md F8). Runs in the jxb container:
#   jxb exec -- /opt/jxb/extras/onnxruntime/build.sh <work-dir> [version]
# FLAVOR=lean (default): no LLM kernel families (TensorRT fused attention cubins, FP4 MoE, fpA x intB GEMM,
#   INT4/FP8 KV cache); enough for CNN detectors like SAM's YOLO models. Contrib ops stay on: ORT's TensorRT EP
#   requires them (EPContext node).
# FLAVOR=full: everything except flash/memory-efficient attention (several hours on a 20-core host).
# Result: <work-dir>/dist/onnxruntime-linux-aarch64-<version>-jp<jetpack>-sm<arch>[-full].tgz
set -euo pipefail
# shellcheck source=/dev/null
source "${JXB_HOME:-/opt/jxb}/toolchain/env.sh"
WORK=$(realpath -m "${1:?usage: build.sh <work-dir> [version]}")
VERSION=${2:-1.31.0}
JOBS=${JOBS:-6}                  # CUDA kernels need ~2 GB RAM per job
FLAVOR=${FLAVOR:-lean}
SRC="$WORK/onnxruntime-$VERSION"
BUILD="$WORK/build-onnxruntime-$VERSION-$JXB_PROFILE"
STAGE="$WORK/stage-onnxruntime-$VERSION-$JXB_PROFILE"
JETPACK=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["board"]["jetpack"])' \
    "$ARM64_SYSROOT/.jxb/profile.json")
NAME="onnxruntime-linux-aarch64-$VERSION-jp$JETPACK-sm$JXB_CUDA_ARCH"
case "$FLAVOR" in
    lean) lean_defines=(onnxruntime_USE_TRT_FUSED_ATTENTION=OFF onnxruntime_USE_FP4_QMOE=OFF
                        onnxruntime_USE_FPA_INTB_GEMM=OFF onnxruntime_USE_INT4_KV_CACHE=OFF onnxruntime_USE_FP8_KV_CACHE=OFF) ;;
    full) NAME="$NAME-full"; lean_defines=(); BUILD="$BUILD-full"; STAGE="$STAGE-full" ;;
    *) echo "FLAVOR must be lean or full" >&2; exit 2 ;;
esac
mkdir -p "$WORK/dist"

if [[ ! -d "$SRC/.git" ]]; then
    git clone --depth 1 --branch "v$VERSION" https://github.com/microsoft/onnxruntime.git "$SRC"
fi

# Flash/memory-efficient attention (transformer kernels) are off: not needed for CNN detectors, and they are the
# most RAM- and time-hungry CUDA kernels in the build.
python3 "$SRC/tools/ci_build/build.py" --build_dir "$BUILD" --config Release --update --build \
    --parallel "$JOBS" --nvcc_threads 1 --cmake_generator Ninja \
    --build_shared_lib --skip_tests --skip_submodule_sync --compile_no_warning_as_error \
    --use_cuda --cuda_home "/usr/local/cuda-$JXB_CUDA_VERSION" --cudnn_home "$ARM64_SYSROOT/usr" \
    --use_tensorrt --use_tensorrt_builtin_parser --tensorrt_home "$ARM64_SYSROOT/usr" \
    --cmake_extra_defines \
        CMAKE_TOOLCHAIN_FILE="$JXB_TOOLCHAIN_FILE" \
        CMAKE_CUDA_ARCHITECTURES="$JXB_CUDA_ARCH" \
        onnxruntime_USE_FLASH_ATTENTION=OFF \
        onnxruntime_USE_MEMORY_EFFICIENT_ATTENTION=OFF \
        onnxruntime_BUILD_UNIT_TESTS=OFF \
        CMAKE_INSTALL_PREFIX=/opt/onnxruntime \
        "${lean_defines[@]}"

rm -rf "$STAGE" && mkdir -p "$STAGE"
DESTDIR="$STAGE" cmake --install "$BUILD/Release"
root="$STAGE/opt/onnxruntime"
# Release layout: flat include/ (what SAM's CMake uses) plus include/onnxruntime/ for find_package users.
mv "$root/include/onnxruntime" "$root/include.tmp" && rmdir "$root/include" && mv "$root/include.tmp" "$root/include"
mkdir "$root/include/onnxruntime"
for f in "$root"/include/*; do
    [[ "$(basename "$f")" == onnxruntime ]] || ln -s "../$(basename "$f")" "$root/include/onnxruntime/"
done
cp "$BUILD"/Release/libonnxruntime_providers_*.so "$root/lib/" 2>/dev/null || true
echo "$VERSION" > "$root/VERSION_NUMBER"
tar -C "$STAGE/opt" --transform "s,^onnxruntime,$NAME," -czf "$WORK/dist/$NAME.tgz" onnxruntime
jxb-elfcheck "$root" --extra-libdir "$root/lib"
ls -la "$WORK/dist/$NAME.tgz"
