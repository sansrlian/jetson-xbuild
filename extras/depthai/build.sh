#!/usr/bin/env bash
# Cross-builds depthai-core from its ROS release source for profiles whose apt repos lack it (ROS 2 Lyrical on
# Ubuntu 24.04 from NVIDIA's Isaac ROS Buildfarm, docs/findings.md F32). Built like the Debian package: CMake with
# CMAKE_INSTALL_PREFIX=/opt/ros/<distro> into a staging dir, then packed for the sysroot (extra) and the board.
# depthai's configure step bootstraps vcpkg for its bundled dependencies; they are built for arm64 with a triplet
# that chainloads the jxb toolchain.
#   jxb exec -- /opt/jxb/extras/depthai/build.sh <work-dir> [release-version]
# Result: <work-dir>/dist/depthai-<version>-<ros_distro>-aarch64-jp<jetpack>.tgz (contents of /opt/ros/<distro>)
set -euo pipefail
# shellcheck source=/dev/null
source "${JXB_HOME:-/opt/jxb}/toolchain/env.sh"
# shellcheck source=/dev/null
source "${JXB_HOME:-/opt/jxb}/toolchain/ros-cross-env.sh"
WORK=$(realpath -m "${1:?usage: build.sh <work-dir> [release-version]}")
VERSION=${2:-3.10.0-1}
SRC="$WORK/depthai-core-$ROS_DISTRO-$VERSION"
BUILD="$WORK/build-depthai-$VERSION-$JXB_PROFILE"
STAGE="$WORK/stage-depthai-$VERSION-$JXB_PROFILE"
TRIPLETS="$WORK/vcpkg-triplets"
JETPACK=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["board"]["jetpack"])' \
    "$ARM64_SYSROOT/.jxb/profile.json")
NAME="depthai-$VERSION-$ROS_DISTRO-aarch64-jp$JETPACK"
mkdir -p "$WORK/dist" "$TRIPLETS"

if [[ ! -d "$SRC/.git" ]]; then
    git clone --depth 1 --branch "release/$ROS_DISTRO/depthai/$VERSION" \
        https://github.com/luxonis/depthai-core-release.git "$SRC"
fi

cat > "$TRIPLETS/arm64-linux-jxb.cmake" <<EOF
set(VCPKG_TARGET_ARCHITECTURE arm64)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE static)
set(VCPKG_CMAKE_SYSTEM_NAME Linux)
set(VCPKG_CHAINLOAD_TOOLCHAIN_FILE "$JXB_TOOLCHAIN_FILE")
set(VCPKG_ENV_PASSTHROUGH ARM64_SYSROOT ROS_DISTRO JXB_PROFILE JXB_HOME JXB_CPU_FLAGS JXB_CUDA_VERSION JXB_CUDA_ARCH
    JXB_CUDA_TARGET JXB_PYTHON_VERSION)
EOF
export VCPKG_OVERLAY_TRIPLETS="$TRIPLETS"
# vcpkg builds its host tools natively: they must not get the board compiler from ros-cross-env.sh.
unset CC CXX

cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_TOOLCHAIN_FILE="$JXB_TOOLCHAIN_FILE" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="/opt/ros/$ROS_DISTRO" -DBUILD_TESTING=OFF \
    -DVCPKG_TARGET_TRIPLET=arm64-linux-jxb -DVCPKG_HOST_TRIPLET=x64-linux \
    `# SAM's OAK node only needs the core (Camera, StereoDepth, IMU, Sync): skip the heavy optional features` \
    -DDEPTHAI_BASALT_SUPPORT=OFF -DDEPTHAI_ENABLE_MP4V2=OFF -DDEPTHAI_DYNAMIC_CALIBRATION_SUPPORT=OFF \
    -DDEPTHAI_ENABLE_APRIL_TAG=OFF -DDEPTHAI_XTENSOR_SUPPORT=OFF -DDEPTHAI_ENABLE_REMOTE_CONNECTION=OFF \
    -DDEPTHAI_ENABLE_PROTOBUF=OFF -DDEPTHAI_ENABLE_BACKWARD=OFF
cmake --build "$BUILD" -j "${JOBS:-8}"
rm -rf "$STAGE" && DESTDIR="$STAGE" cmake --install "$BUILD"
tar -C "$STAGE/opt/ros/$ROS_DISTRO" --transform "s,^\\.,$NAME," -czf "$WORK/dist/$NAME.tgz" .
jxb-elfcheck "$STAGE/opt/ros/$ROS_DISTRO"
ls -la "$WORK/dist/$NAME.tgz"
