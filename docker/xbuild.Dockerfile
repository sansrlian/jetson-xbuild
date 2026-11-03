# jxb xbuild image: x86_64 host tools for cross-building ROS 2 / CUDA / TensorRT code for Jetson Orin.
#
# The image holds only host-side tools. Everything target-side (aarch64 libraries, CUDA target libraries,
# TensorRT, ROS 2 for the board) lives in the sysroot, which is built from the board's own apt repos
# (see sysroot/) and mounted at runtime. Build through the CLI so the arguments match the profile:
#   ./jxb image build --profile jp721-jazzy
# Host Ubuntu = the board userspace's Ubuntu (24.04 for JetPack 7.2.1 native ROS, 26.04 for the Lyrical container).
ARG UBUNTU=24.04
FROM ubuntu:${UBUNTU}

ARG ROS_DISTRO=jazzy
# Repo + key of the ROS distro packages, and (optionally) a separate repo for the distro-independent tools
# (colcon, rosdep). Lyrical on Ubuntu 24.04 comes from NVIDIA's Isaac ROS Buildfarm, the tools from packages.ros.org.
ARG ROS_APT_URL=http://packages.ros.org/ros2/ubuntu
ARG ROS_APT_KEY_URL=https://raw.githubusercontent.com/ros/rosdistro/master/ros.key
ARG ROS_TOOLS_APT_URL=
# CUDA host compiler; its version must match the board's cudart exactly (see profile).
ARG CUDA_DASH=13-2
ARG CUDA_PKG_VERSION=13.2.86-1
ARG CUDA_TARGET=sbsa-linux
ARG USERNAME=dev
ARG USER_UID=1000
ARG USER_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Europe/Berlin

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Base build tools, the aarch64 cross toolchain (the distro's default GCC = the board userspace's compiler) and
# helpers. qemu-user-static became qemu-user (static binaries, no -static suffix) after Ubuntu 24.04.
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash-completion build-essential ca-certificates ccache cmake curl file gdb-multiarch git gnupg \
        jq less lsb-release ninja-build openssh-client patchelf pkg-config python3 python3-pip python3-venv zip \
        python3-numpy python3-yaml python3-onnx protobuf-compiler rsync sudo tzdata unzip \
        vim-tiny wget xz-utils zstd \
        gcc-aarch64-linux-gnu g++-aarch64-linux-gnu binutils-aarch64-linux-gnu \
    && (apt-get install -y --no-install-recommends qemu-user-static || apt-get install -y --no-install-recommends qemu-user) \
    && { [ -e /usr/bin/qemu-aarch64-static ] || ln -s qemu-aarch64 /usr/bin/qemu-aarch64-static; } \
    && rm -rf /var/lib/apt/lists/*

# ROS 2 on the host: colcon, rosdep and the arch-independent code generators (rosidl, ament). The aarch64
# libraries come from the sysroot, which must be built from the same ROS sync.
RUN codename=$(. /etc/os-release && echo "$VERSION_CODENAME") \
    && curl -fsSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg \
    && curl -fsSL "${ROS_APT_KEY_URL}" -o /tmp/distro.key \
    && if grep -q "BEGIN PGP" /tmp/distro.key; then gpg --dearmor < /tmp/distro.key > /usr/share/keyrings/ros-distro-keyring.gpg; \
       else cp /tmp/distro.key /usr/share/keyrings/ros-distro-keyring.gpg; fi \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/ros-distro-keyring.gpg] ${ROS_APT_URL} ${codename} main" \
        > /etc/apt/sources.list.d/ros2.list \
    && if [ -n "${ROS_TOOLS_APT_URL}" ]; then \
        echo "deb [arch=amd64 signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] ${ROS_TOOLS_APT_URL} ${codename} main" \
            > /etc/apt/sources.list.d/ros2-tools.list; fi \
    && apt-get update && apt-get install -y --no-install-recommends \
        ros-${ROS_DISTRO}-ros-base ros-${ROS_DISTRO}-rosidl-default-generators ros-dev-tools \
    && rm -rf /var/lib/apt/lists/* /tmp/distro.key

# x86_64 nvcc. Target headers and libraries (targets/sbsa-linux) are bind-mounted from the sysroot at runtime,
# so the device code links against exactly what the board runs. The board's CUDA 13.2 nvcc exists only in the
# ubuntu2404 repo; its packages also install on newer Ubuntu.
RUN curl -fsSL https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb \
        -o /tmp/cuda-keyring.deb \
    && dpkg -i /tmp/cuda-keyring.deb && rm /tmp/cuda-keyring.deb \
    && apt-get update && apt-get install -y --no-install-recommends \
        cuda-nvcc-${CUDA_DASH}=${CUDA_PKG_VERSION} \
        cuda-crt-${CUDA_DASH}=${CUDA_PKG_VERSION} \
        cuda-cudart-dev-${CUDA_DASH}=${CUDA_PKG_VERSION} \
        cuda-cudart-${CUDA_DASH}=${CUDA_PKG_VERSION} \
        cuda-driver-dev-${CUDA_DASH}=${CUDA_PKG_VERSION} \
        cuda-cuobjdump-${CUDA_DASH} cuda-nvdisasm-${CUDA_DASH} cuda-nvprune-${CUDA_DASH} \
    && apt-mark hold cuda-nvcc-${CUDA_DASH} cuda-crt-${CUDA_DASH} cuda-cudart-dev-${CUDA_DASH} \
    && rm -rf /var/lib/apt/lists/* \
    && cd /usr/local/cuda-${CUDA_DASH/-/.} \
    && ln -sfn targets/${CUDA_TARGET}/include include && ln -sfn targets/${CUDA_TARGET}/lib lib64 \
    && sed -i "1i _TARGET_DIR_ = targets/${CUDA_TARGET}" bin/nvcc.profile
# ^ This toolkit only ever targets the board. The board's target dir is nvcc's default (also for CMake's
#   FindCUDAToolkit probe, which runs nvcc without flags), and plain `nvcc` uses the aarch64 host compiler.
ENV NVCC_CCBIN=aarch64-linux-gnu-g++

# Build-time code generators that run on the host: acados_template + casadi (SAM's acados_model_package renders
# and compiles its solver at build time). /opt/acados-host is the ACADOS_SOURCE_DIR for that: x86 t_renderer,
# everything else points to /opt/acados, which the dev container maps to the sysroot's (board) acados.
# pip installs into /opt/codegen-python (cross-colcon-build adds it to PYTHONPATH); the numpy pip pulls in is
# removed again so the distro's numpy is used, which the distro's scipy/matplotlib are built against.
ARG T_RENDERER_VERSION=0.2.0
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-scipy python3-matplotlib python3-deprecated python3-tqdm \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --no-cache-dir --break-system-packages --target /opt/codegen-python \
        "casadi==3.7.2" "pydantic>=2.12" "pydantic-yaml>=1.6" "typer>=0.20" \
    && rm -rf /opt/codegen-python/numpy /opt/codegen-python/numpy-* /opt/codegen-python/numpy.libs \
    && mkdir -p /opt/acados-host/bin \
    && curl -fsSL -o /opt/acados-host/bin/t_renderer \
        "https://github.com/acados/tera_renderer/releases/download/v${T_RENDERER_VERSION}/t_renderer-v${T_RENDERER_VERSION}-linux-amd64" \
    && chmod +x /opt/acados-host/bin/t_renderer \
    && for d in include lib cmake interfaces; do ln -s "/opt/acados/$d" "/opt/acados-host/$d"; done

# Non-root user matching the host UID/GID, so files written into mounted workspaces keep the host owner.
RUN (getent passwd ${USER_UID} | cut -d: -f1 | xargs -r userdel -r) \
    && (getent group ${USER_GID} | cut -d: -f1 | xargs -r groupdel) || true; \
    groupadd --gid ${USER_GID} ${USERNAME} \
    && useradd --uid ${USER_UID} --gid ${USER_GID} -m -s /bin/bash ${USERNAME} \
    && echo "${USERNAME} ALL=(root) NOPASSWD:ALL" > /etc/sudoers.d/${USERNAME} \
    && chmod 0440 /etc/sudoers.d/${USERNAME} \
    && rosdep init

# The jxb tool tree (toolchain files, wrappers, sysroot builder) is bind-mounted at /opt/jxb, so edits in the
# repo apply without rebuilding the image. These hooks load it into every login and interactive shell.
RUN echo '[ -f /opt/jxb/docker/jxb-profile.sh ] && . /opt/jxb/docker/jxb-profile.sh' > /etc/profile.d/jxb.sh \
    && echo '[ -f /opt/jxb/docker/jxb-profile.sh ] && . /opt/jxb/docker/jxb-profile.sh' >> /etc/bash.bashrc

ENV ROS_DISTRO=${ROS_DISTRO} \
    JXB_HOME=/opt/jxb \
    CCACHE_DIR=/home/${USERNAME}/.ccache

USER ${USERNAME}
WORKDIR /home/${USERNAME}
CMD ["sleep", "infinity"]
