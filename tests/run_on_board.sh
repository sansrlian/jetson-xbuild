#!/usr/bin/env bash
# Runs the cross-built jxb tests on the board (copied there by `jxb test --board NAME`). Read-only for the
# board: everything runs from this directory, nothing is installed.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd)
export LD_LIBRARY_PATH="$D/lib:${LD_LIBRARY_PATH:-}"
results=()
overall=0

step() {
    local name="$1"; shift
    printf '\n=== %s\n' "$name"
    if "$@"; then results+=("PASS  $name"); else results+=("FAIL  $name"); overall=1; fi
}

# ROS setup scripts read unset variables (AMENT_TRACE_SETUP_FILES, ...): source them without nounset.
# The underlay is the board's /opt/ros/<distro>, or the copy jxb shipped for distros the board lacks (JXB_ROS_SETUP);
# the test workspaces' local_setup adds only themselves.
ros_source() {
    set +u
    # shellcheck source=/dev/null
    source "${JXB_ROS_SETUP:-/opt/ros/${ROS_DISTRO:-jazzy}/setup.bash}" && source "$1"
    local rc=$?
    set -u
    return $rc
}

ros_detector() {
    ros_source "$D/ros/local_setup.bash" || return 1
    timeout 30 ros2 run jxb_test_node detector 2>&1 | tee /tmp/jxb_detector.log
    grep -q "published gpu-ok" /tmp/jxb_detector.log
}

ort_ep() {
    # exit 3 = EP not compiled into this ONNX Runtime (e.g. the CPU-only release tarball): skip, don't fail
    "$D/bin/ort_smoke" "$D/models/affine.onnx" --ep "$1"
    local rc=$?
    if [[ $rc -eq 3 ]]; then echo "SKIP: $1 EP not in this ONNX Runtime build"; return 0; fi
    return $rc
}

ros_python() {
    ros_source "$D/ros/local_setup.bash" || return 1
    python3 -c 'from jxb_test_msgs.msg import Detection; print(Detection(label="board", score=1.0))'
}

isaac_cuda_buffer() {
    ros_source "$D/isaac/local_setup.bash" || return 1
    "$D/isaac/jxb_isaac_test/lib/jxb_isaac_test/cuda_buffer_check"
}

head -1 /etc/nv_tegra_release 2>/dev/null
step cuda_hello "$D/bin/cuda_hello"
step trt_engine "$D/bin/trt_smoke" "$D/models/affine.onnx" /tmp/jxb_affine.engine
step ort_cpu "$D/bin/ort_smoke" "$D/models/affine.onnx"
step ort_cuda ort_ep cuda
step ort_tensorrt ort_ep tensorrt
step ort_bench "$D/bin/ort_bench" "$D/models/affine.onnx" --ep cuda --iters 20 --warmup 2
step onnx_check "$D/bin/onnx_check" "$D/models/affine.onnx"
step ros_detector ros_detector
step ros_python ros_python
if [[ -d "$D/isaac" ]]; then
    step isaac_cuda_buffer isaac_cuda_buffer
fi

echo
printf '%s\n' "${results[@]}"
exit $overall
