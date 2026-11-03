#!/bin/bash
# Jetson Orin xbuild entrypoint — UID reconcile, PRODUCT toolchain select, VS Code seed.
set -euo pipefail

ROS2_WS_DIR="${ROS2_WS:-/home/ubuntu/ros2_ws}"
if [ -d "$ROS2_WS_DIR" ] && [ -z "${ENTRYPOINT_WS_RECONCILED:-}" ]; then
  ws_uid="$(stat -c %u "$ROS2_WS_DIR" 2>/dev/null || echo -1)"
  ws_gid="$(stat -c %g "$ROS2_WS_DIR" 2>/dev/null || echo -1)"
  if [ "$ws_uid" != "-1" ] && { [ "$ws_uid" != "$(id -u)" ] || [ "$ws_gid" != "$(id -g)" ]; }; then
    echo "[INFO] Reconciling 'ubuntu' with workspace owner ${ws_uid}:${ws_gid}..."
    exec sudo bash -c '
      set -u
      ws_uid=$1; ws_gid=$2; cur_uid=$3; cur_gid=$4; ws=$5; self=$6; shift 6
      if [ "$ws_uid" = 0 ]; then
        chown "$cur_uid:$cur_gid" "$ws" 2>/dev/null || true
        drop_uid=$cur_uid; drop_gid=$cur_gid
      else
        sed -i -E "s/^(ubuntu:[^:]*:)[0-9]+:[0-9]+:/\1${ws_uid}:${ws_gid}:/" /etc/passwd 2>/dev/null || true
        sed -i -E "s/^(ubuntu:[^:]*:)[0-9]+:/\1${ws_gid}:/" /etc/group 2>/dev/null || true
        find /home/ubuntu -xdev \( -uid "$cur_uid" -o -gid "$cur_gid" \) \
          -exec chown -h "$ws_uid:$ws_gid" {} + 2>/dev/null || true
        drop_uid=$ws_uid; drop_gid=$ws_gid
      fi
      exec setpriv --reuid "$drop_uid" --regid "$drop_gid" --init-groups -- \
        env ENTRYPOINT_WS_RECONCILED=1 "$self" "$@"
    ' bash "$ws_uid" "$ws_gid" "$(id -u)" "$(id -g)" "$ROS2_WS_DIR" "$0" "$@"
  fi
fi

: "${ARM64_SYSROOT:?ARM64_SYSROOT is not set}"

if [ -d "${ARM64_SYSROOT}/etc" ]; then
  echo "Updating DNS configuration in sysroot..."
  sudo mkdir -p "${ARM64_SYSROOT}/etc"
  sudo cp /etc/resolv.conf "${ARM64_SYSROOT}/etc/resolv.conf" 2>/dev/null \
    || echo "[WARN] Could not update DNS in sysroot."
fi

TOOLCHAIN_DIR="${TOOLCHAINS_WS:-/home/ubuntu/toolchains}"

for pair in \
  "arm64-chroot.sh:arm64-chroot" \
  "sysroot-rosdep-install.sh:sysroot-rosdep-install" \
  "sysroot-fix.py:sysroot-fix" \
  "cross-colcon-build.sh:cross-colcon-build" \
  "scripts/board:board"; do
  src="$TOOLCHAIN_DIR/${pair%:*}"
  dst="/usr/local/bin/${pair#*:}"
  [ -e "$src" ] && sudo ln -sfn "$src" "$dst"
done

# Select Orin CMake toolchain from PRODUCT
product="${PRODUCT:-ORIN_AGX}"
cd "$TOOLCHAIN_DIR" || true
case "$product" in
  ORIN_NANO|orin_nano|nano)
    src=orin_nano_cross.cmake; product=ORIN_NANO ;;
  ORIN_NX|orin_nx|nx)
    src=orin_nx_cross.cmake; product=ORIN_NX ;;
  ORIN_AGX|orin_agx|agx|ORIN|orin)
    src=orin_agx_cross.cmake; product=ORIN_AGX ;;
  *)
    echo "[WARN] Unknown PRODUCT='$product' — defaulting to ORIN_AGX"
    src=orin_agx_cross.cmake; product=ORIN_AGX ;;
esac
export PRODUCT="$product"
if [ -f "$TOOLCHAIN_DIR/$src" ]; then
  cp "$TOOLCHAIN_DIR/$src" "$TOOLCHAIN_DIR/cross.cmake"
  echo "[INFO] PRODUCT=$PRODUCT → $src"
fi

# Seed user-local sysroot fixups
APPEND_TEMPLATE="$TOOLCHAIN_DIR/sysroot-fix-append.template.yaml"
APPEND_FILE="$TOOLCHAIN_DIR/sysroot-fix-append.yaml"
if [ -f "$APPEND_TEMPLATE" ] && [ ! -f "$APPEND_FILE" ]; then
  cp "$APPEND_TEMPLATE" "$APPEND_FILE"
fi

# VS Code settings merge (preserve TARGET_* / BOARD_* keys)
VSCODE_DIR="$TOOLCHAIN_DIR/.vscode"
TEMPLATE="$VSCODE_DIR/settings.template.json"
SETTINGS="$VSCODE_DIR/settings.json"
if [ -f "$TEMPLATE" ] && command -v python3 >/dev/null 2>&1; then
  python3 - "$TEMPLATE" "$SETTINGS" <<'PY' || true
import json, os, re, sys
USER_KEYS = [
  "TARGET_IP", "TARGET_GDB_PORT", "TARGET_USER", "TARGET_PASSWORD",
  "TARGET_ROS2_WS", "NODE_PACKAGE_NAME", "NODE_EXECUTABLE_NAME",
  "LAUNCH_PACKAGE_NAME", "LAUNCH_FILE_NAME", "ACTIVE_BOARD", "BOARDS_FILE",
]
def load_jsonc(path):
  text = open(path, encoding="utf-8").read()
  text = re.sub(r"(^|\s)//[^\n]*", r"\1", text)
  text = re.sub(r",(\s*[}\]])", r"\1", text)
  return json.loads(text)
template, settings = sys.argv[1], sys.argv[2]
overrides = {}
if os.path.exists(settings):
  user = load_jsonc(settings)
  overrides = {k: user[k] for k in USER_KEYS if k in user}
key_re = re.compile(r'^(\s*"([^"]+)"\s*:\s*)("(?:[^"\\]|\\.)*"|[^,]*?)(,?\s*)$')
out = []
with open(template, encoding="utf-8") as f:
  for line in f:
    if line.lstrip().startswith('"#"'):
      continue
    m = key_re.match(line.rstrip("\n"))
    if m and m.group(2) in overrides:
      line = m.group(1) + json.dumps(overrides[m.group(2)], ensure_ascii=False) + m.group(4) + "\n"
    out.append(line)
open(settings + ".tmp", "w", encoding="utf-8").writelines(out)
os.replace(settings + ".tmp", settings)
print("[INFO] Seeded/merged .vscode/settings.json")
PY
fi

link_skill() {
  if [ -L "$2" ] || [ ! -e "$2" ]; then
    ln -sfn "$1" "$2"
  else
    echo "[WARN] $2 exists and is not a symlink — leaving in place"
  fi
}

if [ -d "$ROS2_WS_DIR" ] && [ "$ROS2_WS_DIR" != "$TOOLCHAIN_DIR" ]; then
  link_skill "$TOOLCHAIN_DIR/.vscode" "$ROS2_WS_DIR/.vscode"
  link_skill "$TOOLCHAIN_DIR/AGENTS.md" "$ROS2_WS_DIR/AGENTS.md" 2>/dev/null || true
fi

# Active board hint
if [ -f /home/ubuntu/.config/jetson-xbuild/active_board ]; then
  echo "[INFO] Active board: $(cat /home/ubuntu/.config/jetson-xbuild/active_board)"
fi

if [ ! -d "$ARM64_SYSROOT/usr" ] && [ ! -d "$ARM64_SYSROOT/lib" ]; then
  echo "[WARN] Sysroot at $ARM64_SYSROOT looks empty. Mount a JetPack rootfs via --sysroot."
fi

echo "--- Jetson xbuild startup complete (ROS_DISTRO=${ROS_DISTRO:-?} PRODUCT=$PRODUCT) ---"
exec "$@"
