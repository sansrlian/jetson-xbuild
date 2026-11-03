#!/bin/bash
# -----------------------------------------------------------------------------
# Description:
#   This script installs ROS2 packages into the ARM64 sysroot environment.
# Input Arguments:
#   1. Path to the ROS2 workspace directory (default: /home/$USER/ros2_ws)
# -----------------------------------------------------------------------------
set -e

CURRENT_WS="${1:-$ROS2_WS}"

# Colors for better readability (if terminal supports it)
if [ -t 1 ]; then
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    RED='\033[0;31m'
    NC='\033[0m' # No Color
else
    GREEN='' YELLOW='' BLUE='' RED='' NC=''
fi

# Logging functions
log_info() {
    printf "${BLUE}[INFO]${NC} %s\n" "$1"
}

log_success() {
    printf "${GREEN}[SUCCESS]${NC} %s\n" "$1"
}

log_error() {
    printf "${RED}[ERROR]${NC} %s\n" "$1" >&2
}

log_step() {
    printf "\n${YELLOW}=== %s ===${NC}\n" "$1"
}

echo "Installing ROS2 packages..."

# Function to remove exec_depend lines from package.xml files
remove_exec_depends() {
    local workspace_path="$1"

    log_step "Removing exec_depend entries from package.xml files"
    log_info "This can improve the speed of rosdep install by avoiding unnecessary runtime dependencies."

    # Find all package.xml files and remove exec_depend lines. The copied
    # workspace lives inside the sysroot and may be root-owned after rsync.
    sudo find "${workspace_path}/src" -name "package.xml" -type f -print0 | while IFS= read -r -d '' xml_file; do
        sudo sed -i '/<exec_depend>/d' "$xml_file"
    done

    printf "Removed exec_depend entries from package.xml files in ${workspace_path}/src\n"
}

# Copy ROS2 workspace to ARM64 SYSROOT
if [ -d "${CURRENT_WS}" ]; then

    # Check if the 'src' folder exists in the provided workspace
    if [ ! -d "${CURRENT_WS}/src" ]; then
        log_error "ROS2 workspace exists, but the 'src' folder was not found!"
        log_error "Expected src folder at: ${CURRENT_WS}/src"
        log_error "Please create the src folder or provide a valid ROS2 workspace."
        exit 1
    fi

    log_step "Copying ROS2 workspace to ARM64_SYSROOT"
    log_info "Source: ${CURRENT_WS}"
    log_info "Target: $ARM64_SYSROOT"

    CHROOT_WS="${ARM64_SYSROOT}/home/ubuntu/ros2_ws"
    sudo mkdir -p "${CHROOT_WS}"

    # Copy first
    log_info "Syncing files..."
    sudo rsync -az --delete --exclude='.git' \
        "${CURRENT_WS}/src/" "${CHROOT_WS}/src/"
    log_success "Workspace copied successfully"

    # Remove exec_depend from copied files
    remove_exec_depends "${CHROOT_WS}"

    # Only install build-time dependencies
    log_step "Installing build-time dependencies with rosdep"
    log_info "This may take several minutes..."
    log_info "Please wait..."

    arm64-chroot bash -c "export ROS_VERSION=2 && \
export ROS_PYTHON_VERSION=3 && \
export ROS_DISTRO=jazzy && \
rosdep install --from-paths /home/ubuntu/ros2_ws/src --ignore-src -r -y"

    log_success "ROS2 packages installation completed successfully!"

    log_step "Fixing sysroot issues with sysroot-fix script"
    sysroot-fix

    log_success "Sysroot fixes applied!"
else
    log_error "No ROS2 workspace found! Please ensure the workspace exists!"
    log_error "The current workspace path is: ${CURRENT_WS}"
    log_error "Make sure there is the 'src' folder inside the workspace."
    exit 1
fi
