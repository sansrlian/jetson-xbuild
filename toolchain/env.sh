# shellcheck shell=bash
# jxb cross environment. Sourced by every shell in the container (via /etc/profile.d/jxb.sh) and by the wrappers.
# Single source of truth is the profile copy inside the sysroot ($ARM64_SYSROOT/.jxb/profile.json); variables
# already set (e.g. by `jxb up`) win.
if [ -n "${ARM64_SYSROOT:-}" ] && [ -f "${ARM64_SYSROOT}/.jxb/profile.json" ]; then
    while IFS= read -r _jxb_line; do
        _jxb_name=${_jxb_line#export }
        _jxb_name=${_jxb_name%%=*}
        if [ -z "${!_jxb_name:-}" ]; then
            eval "${_jxb_line}"
        fi
    done < <(python3 "${JXB_HOME:-/opt/jxb}/toolchain/profile_env.py" "${ARM64_SYSROOT}/.jxb/profile.json")
    unset _jxb_line _jxb_name
    export JXB_TOOLCHAIN_FILE="${JXB_HOME:-/opt/jxb}/toolchain/orin.toolchain.cmake"
fi
