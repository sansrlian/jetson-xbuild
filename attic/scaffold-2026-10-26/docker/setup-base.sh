#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  build-essential cmake ninja-build ccache gdb gdb-multiarch \
  python3 python3-pip python3-dev python3-venv python3-numpy python3-opencv \
  git git-lfs curl wget ca-certificates sudo locales \
  openssh-client openssh-server sshpass rsync file unzip xz-utils zstd lz4 \
  pkg-config flex bison bc vim inetutils-ping jq yq python3-yaml \
  gcc-aarch64-linux-gnu g++-aarch64-linux-gnu binutils-aarch64-linux-gnu \
  qemu-user-static binfmt-support \
  libssl-dev libffi-dev zlib1g-dev \
  linux-tools-generic \
  htop tmux
# perf may be kernel-version specific; keep generic tools available
rm -rf /var/lib/apt/lists/*
locale-gen en_US en_US.UTF-8
update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
pip3 install --break-system-packages -U \
  colcon-common-extensions vcstool rosdep \
  onnx onnxruntime \
  'numpy<2.3' \
  pyyaml
