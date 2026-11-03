#!/usr/bin/env bash
# Cross-builds acados (with BLASFEO/HPIPM/qpOASES) for Jetson Orin and packs it as /opt/acados, the path SAM's
# trajectory_planner_package links against. The sysroot picks the tarball up as an extra; acados_template's
# build-time code generation then runs on the host via /opt/acados-host (x86 t_renderer, board headers/libs).
#   jxb exec -- /opt/jxb/extras/acados/build.sh <work-dir> [version]
# Result: <work-dir>/dist/acados-<version>-aarch64-jp<jetpack>.tgz (the image provides the host side,
# /opt/acados-host)
set -euo pipefail
# shellcheck source=/dev/null
source "${JXB_HOME:-/opt/jxb}/toolchain/env.sh"
WORK=$(realpath -m "${1:?usage: build.sh <work-dir> [version]}")
VERSION=${2:-0.5.2}
T_RENDERER=${T_RENDERER:-0.2.0}
SRC="$WORK/acados-$VERSION"
BUILD="$WORK/build-acados-$VERSION-$JXB_PROFILE"
STAGE="$WORK/stage-acados-$VERSION-$JXB_PROFILE"
JETPACK=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["board"]["jetpack"])' \
    "$ARM64_SYSROOT/.jxb/profile.json")
NAME="acados-$VERSION-aarch64-jp$JETPACK"
mkdir -p "$WORK/dist"

if [[ ! -d "$SRC/.git" ]]; then
    git clone --branch "v$VERSION" --depth 1 https://github.com/acados/acados.git "$SRC"
    git -C "$SRC" submodule update --init --depth 1 external/blasfeo external/hpipm external/qpoases
fi

# Same options as the old on-board build (install_acados_on_nano.sh), plus the BLASFEO kernel set closest to the
# Orin's Cortex-A78AE.
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_TOOLCHAIN_FILE="$JXB_TOOLCHAIN_FILE" -DCMAKE_BUILD_TYPE=Release \
    -DACADOS_WITH_QPOASES=ON -DACADOS_WITH_OSQP=OFF -DACADOS_INSTALL_DIR=/opt/acados \
    -DCMAKE_INSTALL_PREFIX=/opt/acados -DBLASFEO_TARGET=ARMV8A_ARM_CORTEX_A76 -DHPIPM_TARGET=GENERIC
cmake --build "$BUILD" -j "${JOBS:-8}"
rm -rf "$STAGE" && DESTDIR="$STAGE" cmake --install "$BUILD"
root="$STAGE/opt/acados"
mkdir -p "$root/interfaces" "$root/bin"
cp -a "$SRC/interfaces/acados_template" "$root/interfaces/"
rm -rf "$root/interfaces/acados_template/tera_renderer"
curl -fsSL -o "$root/bin/t_renderer" \
    "https://github.com/acados/tera_renderer/releases/download/v$T_RENDERER/t_renderer-v$T_RENDERER-linux-arm64"
chmod +x "$root/bin/t_renderer"
tar -C "$STAGE/opt" --transform "s,^acados,$NAME," -czf "$WORK/dist/$NAME.tgz" acados
jxb-elfcheck "$root/lib" --extra-libdir "$root/lib"

ls -la "$WORK/dist/$NAME.tgz"
