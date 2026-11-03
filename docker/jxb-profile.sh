# shellcheck shell=bash
# Loaded by every bash shell in the jxb container (login, interactive and `docker exec bash -lc`).
if [ -n "${BASH_VERSION:-}" ]; then
    case ":${PATH}:" in
        *:/opt/jxb/toolchain/bin:*) ;;
        *) export PATH="/opt/jxb/toolchain/bin:/usr/local/cuda/bin:${PATH}" ;;
    esac
    if [ -n "${JXB_HOME:-}" ] && [ -f "${JXB_HOME}/toolchain/env.sh" ]; then
        # shellcheck source=/dev/null
        . "${JXB_HOME}/toolchain/env.sh"
    fi
fi
