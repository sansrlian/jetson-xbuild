#!/usr/bin/env bash
# Cross-builds the jxb test suite for the active profile and checks the results. Runs inside the jxb container:
#   jxb test                       (or: /opt/jxb/tests/run.sh <out-dir>)
# Checks without a board: ELF/linkage check against the sysroot, Python extension suffix, and CPU-only runs
# under qemu-aarch64 (ONNX Runtime, ONNX, TensorRT library loading, generated ROS messages in Python).
# <out-dir>/stage is what `jxb test --board NAME` copies to the board and runs with run_on_board.sh.
set -uo pipefail
# shellcheck source=/dev/null
source "${JXB_HOME:-/opt/jxb}/toolchain/env.sh"
OUT=$(realpath -m "${1:?usage: run.sh <out-dir>}")
T="${JXB_HOME:-/opt/jxb}/tests"
STAGE="$OUT/stage"
SR="$ARM64_SYSROOT"
JOBS=${JOBS:-$(( $(nproc) > 12 ? 12 : $(nproc) ))}
mkdir -p "$OUT/logs" "$STAGE/models" "$STAGE/lib"
results=()
overall=0

step() {
    local name="$1"; shift
    local log="$OUT/logs/$name.log" start=$SECONDS
    printf '\033[36m[test]\033[0m %-14s ' "$name"
    if "$@" >"$log" 2>&1; then
        printf 'PASS (%ss)\n' $((SECONDS - start)); results+=("PASS  $name")
    else
        printf '\033[31mFAIL\033[0m (%ss) -> %s\n' $((SECONDS - start)) "$log"
        tail -n 30 "$log" | sed 's/^/        /'
        results+=("FAIL  $name"); overall=1
    fi
}

cmake_project() {
    local name="$1" build="$OUT/build/$1"
    cmake -S "$T/$name" -B "$build" -G Ninja -DCMAKE_TOOLCHAIN_FILE="$JXB_TOOLCHAIN_FILE" \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$STAGE" &&
        cmake --build "$build" -j "$JOBS" && cmake --install "$build"
}

ros_build() {
    mkdir -p "$OUT/ros_ws" && cd "$OUT/ros_ws" &&
        cross-colcon-build --base-paths "$T/ros_ws/src" --build-base "$OUT/ros_ws/build" \
            --install-base "$STAGE/ros" --event-handlers console_direct+ --parallel-workers "$JOBS"
}

isaac_build() {
    mkdir -p "$OUT/isaac_ws" && cd "$OUT/isaac_ws" &&
        cross-colcon-build --base-paths "$T/isaac_ws/src" --build-base "$OUT/isaac_ws/build" \
            --install-base "$STAGE/isaac" --event-handlers console_direct+ --parallel-workers "$JOBS"
}

py_suffix_check() {
    # Plain .so and .cpython-3XX-aarch64-linux-gnu.so import on the board; a host (x86_64) tag would not.
    local all bad
    all=$(find "$STAGE/ros" -name '*.so' -path '*python3*' -print)
    bad=$(grep -E 'x86_64|amd64' <<<"$all" || true)
    echo "$all"
    [[ -n "$all" && -z "$bad" ]] || { echo "missing or host-tagged extension modules: ${bad:-none found}"; return 1; }
}

# Emulate a CPU close to the board's (profile [board] qemu_cpu): catches code the Orin couldn't run, and
# QEMU 10's default model raises SIGILL on code built for cortex-a78ae (docs/findings.md F25).
qemu() {
    qemu-aarch64-static -cpu "${JXB_QEMU_CPU:-max}" -L "$SR" -E "LD_LIBRARY_PATH=$1" "${@:2}"
}

qemu_ros_python() {
    local py="python${JXB_PYTHON_VERSION}" ros="/opt/ros/${ROS_DISTRO}"
    qemu-aarch64-static -cpu "${JXB_QEMU_CPU:-max}" -L "$SR" \
        -E "PYTHONPATH=$STAGE/ros/jxb_test_msgs/lib/$py/site-packages:$ros/lib/$py/site-packages" \
        -E "LD_LIBRARY_PATH=$STAGE/ros/jxb_test_msgs/lib:$ros/lib" \
        "$SR/usr/bin/$py" -c 'from jxb_test_msgs.msg import Detection; d = Detection(label="x", score=1.0); print(d)'
}

cuda_lib_path="/usr/local/cuda-${JXB_CUDA_VERSION}/targets/${JXB_CUDA_TARGET}/lib:/usr/lib/aarch64-linux-gnu/nvidia"

step models python3 "$T/models/make_models.py" "$STAGE/models"
for p in cuda_hello trt_smoke ort_smoke onnx_lib; do
    step "$p" cmake_project "$p"
done
step ros_ws ros_build
# Isaac ROS test only where the sysroot has Isaac ROS (profiles with the isaac-ros-5 package set).
if [[ -d "$SR/opt/ros/$ROS_DISTRO/share/isaac_ros_tensor_rt" ]]; then
    step isaac_ws isaac_build
fi
cp -a "$SR"/opt/onnxruntime/lib/libonnxruntime*.so* "$STAGE/lib/"
cp "$T/run_on_board.sh" "$STAGE/"
step elfcheck jxb-elfcheck "$STAGE"
step py_suffix py_suffix_check
step qemu_ort qemu "/opt/onnxruntime/lib" "$STAGE/bin/ort_smoke" "$STAGE/models/affine.onnx"
step qemu_onnx qemu "/usr/lib/aarch64-linux-gnu" "$STAGE/bin/onnx_check" "$STAGE/models/affine.onnx"
step qemu_trt_load qemu "$cuda_lib_path" "$STAGE/bin/trt_smoke"
step qemu_ros_py qemu_ros_python

echo
printf '%s\n' "${results[@]}"
echo "stage: $STAGE"
exit $overall
