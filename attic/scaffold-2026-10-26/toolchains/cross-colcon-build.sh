#!/usr/bin/env bash
set -euo pipefail

# Cross compile related settings
export ROS_VERSION=2
export ROS_PYTHON_VERSION=3
export ROS_DISTRO="${ROS_DISTRO:-jazzy}"

# Required external environment:
#   ARM64_SYSROOT

if [[ -z "${ARM64_SYSROOT:-}" ]]; then
    printf '\033[31mERROR:\033[0m ARM64_SYSROOT is not set\n' >&2
    exit 1
fi

# Python path settings
export PYTHONPATH="/usr/lib/python3/dist-packages"
export PYTHONPATH="${PYTHONPATH}:/usr/local/lib/python3.12/dist-packages"
export PYTHONPATH="${PYTHONPATH}:/opt/ros/${ROS_DISTRO}/lib/python3.12/site-packages"
export PYTHONPATH="${PYTHONPATH}:${ARM64_SYSROOT}/opt/ros/${ROS_DISTRO}/lib/python3.12/site-packages"

# Configure CMake prefix path to locate host and target libraries
export CMAKE_PREFIX_PATH="${ARM64_SYSROOT}/usr"
export CMAKE_PREFIX_PATH="${CMAKE_PREFIX_PATH}:${ARM64_SYSROOT}/usr/lib"
export CMAKE_PREFIX_PATH="${CMAKE_PREFIX_PATH}:${ARM64_SYSROOT}/usr/lib/cmake"
export CMAKE_PREFIX_PATH="${CMAKE_PREFIX_PATH}:${ARM64_SYSROOT}/usr/lib/aarch64-linux-gnu"
export CMAKE_PREFIX_PATH="${CMAKE_PREFIX_PATH}:${ARM64_SYSROOT}/usr/lib/aarch64-linux-gnu/cmake"
export CMAKE_PREFIX_PATH="${CMAKE_PREFIX_PATH}:${ARM64_SYSROOT}/usr/share"
export CMAKE_PREFIX_PATH="${CMAKE_PREFIX_PATH}:${ARM64_SYSROOT}/opt/ros/${ROS_DISTRO}"
export CMAKE_PREFIX_PATH="${CMAKE_PREFIX_PATH}:${ARM64_SYSROOT}/opt/ros/${ROS_DISTRO}/lib"
export CMAKE_PREFIX_PATH="${CMAKE_PREFIX_PATH}:${ARM64_SYSROOT}/opt/ros/${ROS_DISTRO}/lib/cmake"
export CMAKE_PREFIX_PATH="${CMAKE_PREFIX_PATH}:${ARM64_SYSROOT}/opt/ros/${ROS_DISTRO}/share"

export AMENT_PREFIX_PATH="${ARM64_SYSROOT}/opt/ros/${ROS_DISTRO}"

# PKG_CONFIG settings for cross-compilation
export PKG_CONFIG_PATH="${ARM64_SYSROOT}/usr/lib/pkgconfig"
export PKG_CONFIG_PATH="${PKG_CONFIG_PATH}:${ARM64_SYSROOT}/usr/lib/aarch64-linux-gnu/pkgconfig"
export PKG_CONFIG_PATH="${PKG_CONFIG_PATH}:${ARM64_SYSROOT}/usr/share/pkgconfig"
export PKG_CONFIG_PATH="${PKG_CONFIG_PATH}:${ARM64_SYSROOT}/opt/ros/${ROS_DISTRO}/lib/pkgconfig"
export PKG_CONFIG_PATH="${PKG_CONFIG_PATH}:${ARM64_SYSROOT}/opt/ros/${ROS_DISTRO}/lib/aarch64-linux-gnu/pkgconfig"
export PKG_CONFIG_SYSROOT_DIR="${ARM64_SYSROOT}"

# NVIDIA / JetPack hints for packages that probe the environment
export CUDA_TOOLKIT_ROOT_DIR="${ARM64_SYSROOT}/usr/local/cuda"
export CUDA_HOME="${CUDA_TOOLKIT_ROOT_DIR}"
export TensorRT_DIR="${ARM64_SYSROOT}/usr/lib/aarch64-linux-gnu/cmake"
export LD_LIBRARY_PATH="${ARM64_SYSROOT}/usr/local/cuda/targets/aarch64-linux/lib:${ARM64_SYSROOT}/usr/lib/aarch64-linux-gnu:${LD_LIBRARY_PATH:-}"
export PKG_CONFIG_PATH="${PKG_CONFIG_PATH}:${ARM64_SYSROOT}/usr/lib/aarch64-linux-gnu/pkgconfig:${ARM64_SYSROOT}/usr/local/cuda/lib64/pkgconfig"


build_type="${CMAKE_BUILD_TYPE:-Release}"

args=("$@")

is_colcon_arg() {
    local arg="${1:-}"
    case "$arg" in
        -h|--help|\
        --build-base|--build-base=*|\
        --install-base|--install-base=*|\
        --merge-install|\
        --symlink-install|\
        --test-result-base|--test-result-base=*|\
        --continue-on-error|\
        --executor|--executor=*|\
        --parallel-workers|--parallel-workers=*|\
        --event-handlers|--event-handlers=*|\
        --ignore-user-meta|\
        --metas|--metas=*|\
        --base-paths|--base-paths=*|\
        --packages-ignore|--packages-ignore=*|\
        --packages-ignore-regex|--packages-ignore-regex=*|\
        --paths|--paths=*|\
        --packages-up-to|--packages-up-to=*|\
        --packages-up-to-regex|--packages-up-to-regex=*|\
        --packages-above|--packages-above=*|\
        --packages-above-and-dependencies|--packages-above-and-dependencies=*|\
        --packages-above-depth|--packages-above-depth=*|\
        --packages-select-by-dep|--packages-select-by-dep=*|\
        --packages-skip-by-dep|--packages-skip-by-dep=*|\
        --packages-skip-up-to|--packages-skip-up-to=*|\
        --packages-select-build-failed|\
        --packages-skip-build-finished|\
        --packages-select-test-failures|\
        --packages-skip-test-passed|\
        --packages-select|--packages-select=*|\
        --packages-skip|--packages-skip=*|\
        --packages-select-regex|--packages-select-regex=*|\
        --packages-skip-regex|--packages-skip-regex=*|\
        --packages-start|--packages-start=*|\
        --packages-end|--packages-end=*|\
        --allow-overriding|--allow-overriding=*|\
        --cmake-target|--cmake-target=*|\
        --cmake-target-skip-unavailable|\
        --cmake-clean-cache|\
        --cmake-clean-first|\
        --cmake-force-configure|\
        --ament-cmake-args|--ament-cmake-args=*|\
        --catkin-cmake-args|--catkin-cmake-args=*|\
        --catkin-skip-building-tests|\
        --mixin-files|--mixin-files=*|\
        --mixin|--mixin=*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

# Early help intercept:
# behave like colcon top-level help only if help appears before --cmake-args.
for arg in "${args[@]}"; do
    case "$arg" in
        --cmake-args|--cmake-args=*) break ;;
        -h|--help)
            cat <<EOF
Wrapper for 'colcon build' with cross-compile defaults.
Injects toolchain + Python3 paths into --cmake-args.

If --help appears before --cmake-args, it is treated as wrapper/colcon help.
If passed as: --cmake-args " --help", it is forwarded to CMake.

--- colcon build --help ---
EOF
            exec colcon build --help
            ;;
    esac
done

colcon_args=()
user_cmake_args=()

mode="colcon"
for arg in "${args[@]}"; do
    case "$mode" in
        colcon)
            case "$arg" in
                --cmake-args)
                    mode="cmake"
                    ;;
                --cmake-args=*)
                    mode="cmake"
                    val="${arg#--cmake-args=}"
                    [[ -n "$val" ]] && user_cmake_args+=("${val# }")
                    ;;
                *)
                    colcon_args+=("$arg")
                    ;;
            esac
            ;;
        cmake)
            case "$arg" in
                --cmake-args)
                    # keep cmake mode
                    ;;
                --cmake-args=*)
                    val="${arg#--cmake-args=}"
                    [[ -n "$val" ]] && user_cmake_args+=("${val# }")
                    ;;
                " "*)
                    user_cmake_args+=("${arg# }")
                    ;;
                *)
                    if is_colcon_arg "$arg"; then
                        mode="colcon"
                        colcon_args+=("$arg")
                    else
                        user_cmake_args+=("$arg")
                    fi
                    ;;
            esac
            ;;
    esac
done

# Defaults that the user is NOT allowed to override (protected).
protected_cmake_args=(
    -DCMAKE_TOOLCHAIN_FILE=/home/ubuntu/toolchains/cross.cmake
    -DPython3_EXECUTABLE=/usr/bin/python3
    -DPython3_ROOT_DIR=/usr
    -DPython3_FIND_STRATEGY=LOCATION
)
# Keys (without value) of protected args, used to strip user overrides.
protected_keys=(
    -DCMAKE_TOOLCHAIN_FILE
    -DPython3_EXECUTABLE
    -DPython3_ROOT_DIR
    -DPython3_FIND_STRATEGY
)
# Defaults that the user MAY override (e.g. CMAKE_BUILD_TYPE).
overridable_keys=(
    -DCMAKE_BUILD_TYPE
)

arg_key() {
    # Extract the "-DNAME" portion from a "-DNAME=VALUE" (or "-DNAME:TYPE=VALUE") argument.
    local a="$1"
    if [[ "$a" == -D*=* ]]; then
        local head="${a%%=*}"
        printf '%s' "${head%%:*}"
    else
        printf '%s' "$a"
    fi
}

in_list() {
    local needle="$1"; shift
    local item
    for item in "$@"; do
        [[ "$item" == "$needle" ]] && return 0
    done
    return 1
}

# Filter user_cmake_args:
#   - drop any attempt to override protected defaults (with a warning)
#   - keep overrides of overridable defaults, and record which were overridden
filtered_user_cmake_args=()
user_overridden_keys=()
for ua in "${user_cmake_args[@]}"; do
    key="$(arg_key "$ua")"
    if in_list "$key" "${protected_keys[@]}"; then
        printf '\033[33mWARNING:\033[0m ignoring user --cmake-args %q (overriding %q is not allowed)\n' "$ua" "$key" >&2
        continue
    fi
    if in_list "$key" "${overridable_keys[@]}"; then
        user_overridden_keys+=("$key")
    fi
    filtered_user_cmake_args+=("$ua")
done

# Build overridable defaults, skipping those the user already set.
overridable_cmake_args=()
if ! in_list "-DCMAKE_BUILD_TYPE" "${user_overridden_keys[@]}"; then
    overridable_cmake_args+=("-DCMAKE_BUILD_TYPE=${build_type}")
fi

cmd=(colcon build)
cmd+=("${colcon_args[@]}")
cmd+=(--cmake-force-configure --cmake-args)
cmd+=("${protected_cmake_args[@]}")
cmd+=("${overridable_cmake_args[@]}")
cmd+=("${filtered_user_cmake_args[@]}")

printf '\033[34mRunning:\033[0m '
printf '%q ' "${cmd[@]}"
printf '\n'

rc=0
"${cmd[@]}" 2> >(
    grep -Fv "The path '${ARM64_SYSROOT}/opt/ros/${ROS_DISTRO}' in the environment variable AMENT_PREFIX_PATH doesn't contain any 'local_setup.*' files" >&2
) || rc=$?
wait
exit $rc