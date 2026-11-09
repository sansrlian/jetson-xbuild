# shellcheck shell=bash
# Environment for cross-building ROS 2 packages against the jxb sysroot (sourced by cross-colcon-build and by the
# extras/ build scripts that use plain CMake). Requires toolchain/env.sh to have been sourced.
# Code generators run on the host: host ROS python modules first, the sysroot's as fallback (pure python only).
py="python${JXB_PYTHON_VERSION}"
export ROS_VERSION=2 ROS_PYTHON_VERSION=3
export PATH="/opt/ros/${ROS_DISTRO}/bin:${PATH}"
export PYTHONPATH="/opt/ros/${ROS_DISTRO}/lib/${py}/site-packages:${ARM64_SYSROOT}/opt/ros/${ROS_DISTRO}/lib/${py}/site-packages"
export AMENT_PREFIX_PATH="${ARM64_SYSROOT}/opt/ros/${ROS_DISTRO}"
export CMAKE_PREFIX_PATH="${ARM64_SYSROOT}/opt/ros/${ROS_DISTRO}"
# ament vendor packages (gz_*_vendor, ...) install their upstream project under opt/<pkg>, which only their
# environment hook puts on CMAKE_PREFIX_PATH (`find_package(gz-sim)` after `find_package(gz_sim_vendor)`); the
# sysroot's setup files are never sourced, so apply those hooks here.
for _jxb_dsv in "${ARM64_SYSROOT}/opt/ros/${ROS_DISTRO}"/share/*/environment/vendor_package_cmake_prefix.dsv; do
    [[ -f "${_jxb_dsv}" ]] || continue
    while IFS=';' read -r _jxb_op _jxb_var _jxb_rel; do
        if [[ "${_jxb_var}" == CMAKE_PREFIX_PATH && -n "${_jxb_rel}" ]]; then
            CMAKE_PREFIX_PATH="${CMAKE_PREFIX_PATH}:${ARM64_SYSROOT}/opt/ros/${ROS_DISTRO}/${_jxb_rel}"
        fi
    done < "${_jxb_dsv}"
done
unset _jxb_dsv _jxb_op _jxb_var _jxb_rel
# Build steps that compile C/C++ themselves instead of through CMake (e.g. acados_template's generated Makefiles)
# use $CC/$CXX: give them the board compiler. CMake ignores these, the toolchain file sets its compilers.
export JXB_CROSS_BUILD=1     # lets patched host-side tools skip steps that only work on the board (sysroot/fixups.toml)
export CC="aarch64-linux-gnu-gcc --sysroot=${ARM64_SYSROOT} ${JXB_CPU_FLAGS}"
export CXX="aarch64-linux-gnu-g++ --sysroot=${ARM64_SYSROOT} ${JXB_CPU_FLAGS}"
# acados code generation on the host: x86 t_renderer, board headers and libraries (see xbuild.Dockerfile).
if [[ -d /opt/acados/lib && -d /opt/acados-host ]]; then
    export ACADOS_SOURCE_DIR=/opt/acados-host
    export PYTHONPATH="${PYTHONPATH}:/opt/acados-host/interfaces/acados_template"
fi
# Python packages for build-time generators that Ubuntu doesn't ship (casadi, pydantic 2, ...), see image.
if [[ -d /opt/codegen-python ]]; then
    export PYTHONPATH="${PYTHONPATH}:/opt/codegen-python"
fi

