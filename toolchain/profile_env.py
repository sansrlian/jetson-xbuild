#!/usr/bin/env python3
"""Print `export NAME=value` lines for the toolchain environment of a resolved jxb profile (JSON)."""
import json
import shlex
import sys


def profile_env(p: dict) -> dict[str, str]:
    b, i, s = p["board"], p["image"], p["sysroot"]
    return {
        "JXB_PROFILE": p["name"],
        "ROS_DISTRO": b["ros_distro"],
        "JXB_CPU_FLAGS": b["cpu_flags"],
        "JXB_QEMU_CPU": b.get("qemu_cpu", "max"),
        "JXB_CUDA_ARCH": b["cuda_arch"],
        "JXB_CUDA_VERSION": i["cuda_version"],
        "JXB_CUDA_TARGET": s.get("cuda_target", "sbsa-linux"),
        "JXB_PYTHON_VERSION": s.get("python", "3.12"),
    }


if __name__ == "__main__":
    with open(sys.argv[1]) as f:
        env = profile_env(json.load(f))
    for k, v in env.items():
        print(f"export {k}={shlex.quote(v)}")
