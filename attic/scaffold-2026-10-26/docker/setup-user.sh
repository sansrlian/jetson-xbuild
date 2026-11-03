#!/usr/bin/env bash
set -euo pipefail
USERNAME="${1:-ubuntu}"
if ! id "$USERNAME" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "$USERNAME"
fi
echo "$USERNAME ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/$USERNAME"
chmod 0440 "/etc/sudoers.d/$USERNAME"
mkdir -p "/home/$USERNAME/ros2_ws" "/home/$USERNAME/toolchains"
chown -R "$USERNAME:$USERNAME" "/home/$USERNAME"
