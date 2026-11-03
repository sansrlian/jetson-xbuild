#!/usr/bin/env bash
# Host-side NVIDIA tracing / remote profiling clients.
# Full CUDA cross-link libraries come from the *board sysroot*, not here.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# Nsight Systems CLI (x86 host) — used to open/merge reports pulled from boards.
# Package availability varies by Ubuntu; fail soft with a clear note.
apt-get update
if apt-cache show nsight-systems 2>/dev/null | grep -q Package; then
  apt-get install -y --no-install-recommends nsight-systems || true
fi
if apt-cache show nsight-compute 2>/dev/null | grep -q Package; then
  apt-get install -y --no-install-recommends nsight-compute || true
fi
# Fallback: leave stubs; board scripts still collect .nsys-rep via SSH.
rm -rf /var/lib/apt/lists/*

mkdir -p /usr/local/share/jetson-xbuild
cat > /usr/local/share/jetson-xbuild/NVIDIA_TOOLS.txt <<'TXT'
Host container provides gdb-multiarch, ssh/rsync, and optional Nsight packages.
CUDA / TensorRT / cuDNN / VPI / ONNX Runtime for *linking* must live in the
mounted Jetson sysroot (/opt/arm64_sysroot), captured from a JetPack board.

Remote tracing:
  board nsys --duration 15 -- <pkg> <exe>
  board ncu --kernel-regex Regex -- <pkg> <exe>
  board tegra-stats
  board kernel-trace  (trace-cmd / ftrace via SSH when available on board)
TXT
