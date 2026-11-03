#!/usr/bin/env bash
set -euo pipefail
USERNAME="${1:-ubuntu}"
SRC_TOOLCHAINS="${2:-/tmp/toolchains-src}"
DEST="/home/${USERNAME}/toolchains"
rm -rf "$DEST"
mkdir -p "$DEST"
cp -a "$SRC_TOOLCHAINS"/. "$DEST"/
chown -R "${USERNAME}:${USERNAME}" "$DEST"
if [ -f "$DEST/env.conf" ]; then
  cat "$DEST/env.conf" >> "/home/${USERNAME}/.bashrc"
fi
for pair in \
  "entrypoint.sh:entrypoint" \
  "arm64-chroot.sh:arm64-chroot" \
  "sysroot-rosdep-install.sh:sysroot-rosdep-install" \
  "sysroot-fix.py:sysroot-fix" \
  "cross-colcon-build.sh:cross-colcon-build" \
  "scripts/board:board"; do
  ln -sfn "${DEST}/${pair%:*}" "/usr/local/bin/${pair#*:}"
done
