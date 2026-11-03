#!/bin/bash
# -----------------------------------------------------------------------------
# Description:
#   This script provides a chroot environment for the ARM64 sysroot.
#   It uses QEMU only when the container architecture is not ARM64.
# Input Arguments:
#   All arguments are passed to the chroot command.
# -----------------------------------------------------------------------------

set -e

LOCKFILE="/tmp/arm64-chroot.lock"

if [[ -z "${ARM64_SYSROOT:-}" ]]; then
    echo "ARM64_SYSROOT is not set" >&2
    exit 1
fi

if [[ ! -d "$ARM64_SYSROOT" ]]; then
    echo "ARM64_SYSROOT does not exist: $ARM64_SYSROOT" >&2
    exit 1
fi

container_arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
use_qemu=0
if [[ "$container_arch" != "arm64" && "$container_arch" != "aarch64" ]]; then
    use_qemu=1
fi

# Check for existing lock
if [ -f "$LOCKFILE" ]; then
    PID=$(cat "$LOCKFILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Another arm64-chroot instance is running (PID: $PID)"
        echo "Wait for it to finish or kill it with: sudo kill $PID"
        exit 1
    else
        echo "Removing stale lock file"
        sudo rm -f "$LOCKFILE"
    fi
fi

# Create lock
echo $$ | sudo tee "$LOCKFILE" > /dev/null

cleanup() {
    echo "Cleaning up..."
    sudo umount -l "$ARM64_SYSROOT/proc" 2>/dev/null || true
    sudo umount -l "$ARM64_SYSROOT/sys" 2>/dev/null || true
    sudo umount -l "$ARM64_SYSROOT/dev" 2>/dev/null || true

    # Sync after unmount
    sync
    sudo rm -f "$LOCKFILE"
    echo "Cleaning up completed successfully."
}

trap cleanup EXIT INT TERM

mount_chroot() {
    echo "Mounting filesystems..."
    sudo mount -t proc proc "$ARM64_SYSROOT/proc" 2>/dev/null || true
    sudo mount -t sysfs sysfs "$ARM64_SYSROOT/sys" 2>/dev/null || true
    sudo mount -o bind /dev "$ARM64_SYSROOT/dev" 2>/dev/null || true

    echo "Mount chroot completed successfully."
}

# Set non-interactive environment
export DEBIAN_FRONTEND=noninteractive
export LANG=C.UTF-8
export LC_ALL=C.UTF-8

mount_chroot
if [ "$use_qemu" -eq 1 ]; then
    if [ ! -x "$ARM64_SYSROOT/usr/bin/qemu-aarch64-static" ]; then
        echo "qemu-aarch64-static is required for $container_arch containers but was not found in the sysroot." >&2
        exit 1
    fi
    sudo update-binfmts --enable qemu-aarch64 2>/dev/null || true
else
    echo "Native ARM64 container detected; entering sysroot without QEMU."
fi
echo "Entering ARM64 chroot environment..."
printf '\033[32mExecuting command: \033[36msudo'
printf ' %q' "$@"
printf '\033[0m\n'
sudo chroot "$ARM64_SYSROOT" "$@"
