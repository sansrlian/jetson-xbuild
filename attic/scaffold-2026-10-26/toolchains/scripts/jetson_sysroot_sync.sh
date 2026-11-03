#!/usr/bin/env bash
# Capture a Jetson Orin rootfs (with JetPack CUDA/TensorRT/…) into a host sysroot tree.
# Run on the x86 workstation. Requires SSH to a prepared board (ROS + JetPack installed).
set -euo pipefail

BOARD=""
DEST=""
USER_NAME="ubuntu"
PORT=22

usage() {
  cat <<'USAGE'
Usage: jetson_sysroot_sync.sh --board <ip|hostname> --dest <host-dir> [--user ubuntu] [--port 22]

Pulls a usable cross-compile sysroot from a live Orin. Install JetPack + ROS on
the board first so CUDA, TensorRT, cuDNN, VPI, and /opt/ros/$ROS_DISTRO are present.

Example:
  ./toolchains/scripts/jetson_sysroot_sync.sh \
    --board 192.168.100.11 \
    --dest ./sysroots/jp72-orin-agx
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --board) BOARD="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    --user) USER_NAME="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown: $1"; usage; exit 1 ;;
  esac
done

[ -n "$BOARD" ] && [ -n "$DEST" ] || { usage; exit 1; }
mkdir -p "$DEST"

echo "==> Syncing rootfs from ${USER_NAME}@${BOARD} → $DEST"
echo "    This can take a long time and needs sudo on the board for full /."

# Stream a filtered rootfs tarball over SSH.
ssh -p "$PORT" -o StrictHostKeyChecking=accept-new "${USER_NAME}@${BOARD}" \
  'sudo tar -C / \
    --exclude=./dev --exclude=./proc --exclude=./sys --exclude=./tmp --exclude=./run \
    --exclude=./mnt --exclude=./media --exclude=./lost+found \
    --exclude=./var/tmp --exclude=./var/cache/apt --exclude=./var/log \
    --warning=no-file-changed \
    -cf - .' \
  | tar -C "$DEST" -xf -

# Ensure qemu binary present for amd64 chroot later
if [ ! -x "$DEST/usr/bin/qemu-aarch64-static" ]; then
  echo "==> Copying qemu-aarch64-static into sysroot from host (if available)"
  if [ -x /usr/bin/qemu-aarch64-static ]; then
    mkdir -p "$DEST/usr/bin"
    cp /usr/bin/qemu-aarch64-static "$DEST/usr/bin/"
  else
    echo "[WARN] Host lacks qemu-aarch64-static; install qemu-user-static before arm64-chroot."
  fi
fi

# Drop ROS setup scripts that hardcode board paths (Renesas-style relocation aid)
find "$DEST/opt/ros" -maxdepth 2 -type f \( \
  -name '*.bash' -o -name '*.sh' -o -name '*.zsh' -o -name '_local_setup_util.py' \
\) -delete 2>/dev/null || true

echo "==> Sysroot ready: $DEST"
echo "    Mount with: ./setup_jetson_docker.sh kilted --sysroot $DEST ..."
du -sh "$DEST" || true
