#!/usr/bin/env bash
# Install host (amd64) ROS 2 for tooling / message generation while cross-building.
set -euo pipefail
: "${ROS_DISTRO:?}"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends curl gnupg lsb-release
curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
  | gpg --dearmor -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo "$UBUNTU_CODENAME") main" \
  > /etc/apt/sources.list.d/ros2.list
apt-get update
apt-get install -y --no-install-recommends \
  "ros-${ROS_DISTRO}-ros-base" \
  "ros-${ROS_DISTRO}-rmw-cyclonedds-cpp" \
  "ros-${ROS_DISTRO}-rmw-fastrtps-cpp" \
  "ros-${ROS_DISTRO}-cv-bridge" \
  "ros-${ROS_DISTRO}-vision-msgs" \
  "ros-${ROS_DISTRO}-image-transport" \
  "ros-${ROS_DISTRO}-diagnostic-msgs" \
  python3-rosdep python3-rosinstall-generator
rosdep init || true
rosdep update || true
rm -rf /var/lib/apt/lists/*
