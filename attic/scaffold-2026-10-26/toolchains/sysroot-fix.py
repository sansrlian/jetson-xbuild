#!/usr/bin/env python3
import os
import sys
import time
import shutil
import argparse
try:
    import yaml
except ImportError:
    print("ERROR: Missing PyYAML. Install: sudo apt install python3-yaml  (or: python3 -m pip install --user pyyaml)", file=sys.stderr)
    sys.exit(2)

# Resolve the toolchains directory from the container env (set by the
# Dockerfile as TOOLCHAINS_WS=/home/${USERNAME}/toolchains), falling back to
# the conventional path. TOOLCHAINS_WS must be in sudoers env_keep so it
# survives the `sudo -E` re-exec in ensure_root().
TOOLCHAINS_DIR = os.environ.get("TOOLCHAINS_WS", "/home/ubuntu/toolchains")
DEFAULT_YAML = os.path.join(TOOLCHAINS_DIR, "sysroot-fix.yaml")
APPEND_YAML  = os.path.join(TOOLCHAINS_DIR, "sysroot-fix-append.yaml")

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def expand_tokens(s: str, sysroot: str, ros_distro: str) -> str:
    return (
        s.replace("${ROS_DISTRO}", ros_distro)
         .replace("${ARM64_SYSROOT}", sysroot)
    )

def backup_file(path: str) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    bkp = f"{path}.bak.{ts}"
    shutil.copy2(path, bkp)
    return bkp

def ensure_root():
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        os.execvp("sudo", ["sudo", "-E", sys.executable] + sys.argv)

def load_yaml(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        eprint(f"ERROR: {path} root must be a mapping (dict).")
        sys.exit(2)
    return data

def apply_rules(data: dict, sysroot: str, ros_distro: str, dry_run: bool, label: str = ""):
    total_rules = 0
    matched_rules = 0
    errors = 0
    prefix = f"[{label}] " if label else ""

    for pkg, rules in data.items():
        if not isinstance(rules, list):
            eprint(f"ERROR: '{pkg}' must be a list of rules.")
            errors += 1
            continue

        pkg_matched = False
        for r in rules:
            total_rules += 1
            if not isinstance(r, dict):
                eprint(f"ERROR: rule in '{pkg}' must be a dict.")
                errors += 1
                continue

            # 'replace' must be present (an explicit empty string is allowed for
            # deletion, but a missing key is a config error, not a silent delete).
            if "replace" not in r:
                eprint(f"ERROR: rule in '{pkg}' missing 'replace'")
                errors += 1
                continue

            rel_file = expand_tokens(r.get("file", ""), sysroot, ros_distro)
            find_s   = expand_tokens(r.get("find", ""), sysroot, ros_distro)
            repl_s   = expand_tokens(r.get("replace", ""), sysroot, ros_distro)

            if not rel_file or not find_s:
                eprint(f"ERROR: rule in '{pkg}' missing 'file' or 'find'")
                errors += 1
                continue

            target = rel_file if os.path.isabs(rel_file) else os.path.join(sysroot, rel_file)
            if not os.path.isfile(target):
                continue

            with open(target, "r", encoding="utf-8", errors="surrogateescape") as f:
                content = f.read()

            if find_s not in content:
                continue

            if not pkg_matched:
                print(f"\n{prefix}[sysroot-fix] Package: {pkg}")
                pkg_matched = True

            new_content = content.replace(find_s, repl_s)
            print(f"  - PATCH: {target}")
            print(f"    find:    {find_s}")
            print(f"    replace: {repl_s}")

            if not dry_run:
                bkp = backup_file(target)
                with open(target, "w", encoding="utf-8", errors="surrogateescape") as f:
                    f.write(new_content)
                print(f"    backup:  {bkp}")

            matched_rules += 1

    return matched_rules, total_rules, errors

def main():
    ensure_root()

    parser = argparse.ArgumentParser(description="Sysroot CMake path fixer")
    parser.add_argument("packages", nargs="*", help="Only process specific packages")
    parser.add_argument("--yaml", help=f"Override default YAML (default: {DEFAULT_YAML})")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be patched without writing")
    parser.add_argument("--list", action="store_true", help="List available packages in YAML")
    args = parser.parse_args()

    sysroot = os.environ.get("ARM64_SYSROOT")
    ros_distro = os.environ.get("ROS_DISTRO", "jazzy")

    if not sysroot:
        eprint("ERROR: ARM64_SYSROOT is not set.")
        return 2
    if not os.path.isdir(sysroot):
        eprint(f"ERROR: ARM64_SYSROOT directory does not exist: {sysroot}")
        return 2

    main_yaml = args.yaml or os.environ.get("SYSROOT_FIX_YAML") or DEFAULT_YAML

    if args.list:
        data = load_yaml(main_yaml)
        print("Packages:")
        for k in sorted(data.keys()):
            print(f"  - {k}")
        return 0

    print(f"[sysroot-fix] YAML= {main_yaml}")
    print(f"[sysroot-fix] ARM64_SYSROOT= {sysroot}")
    print(f"[sysroot-fix] ROS_DISTRO= {ros_distro}")
    print(f"[sysroot-fix] dry_run= {args.dry_run}")

    # A missing main YAML is a hard error: the tool was asked to apply fixes but
    # the config it needs is gone. Returning success here would let a broken
    # cross-build proceed with un-relativized absolute paths.
    if not os.path.isfile(main_yaml):
        eprint(f"ERROR: YAML file not found: {main_yaml}")
        return 2

    data = load_yaml(main_yaml)

    selected_pkgs = set(args.packages) if args.packages else None
    if selected_pkgs:
        data = {k: v for k, v in data.items() if k in selected_pkgs}

    matched, total, errors = apply_rules(data, sysroot, ros_distro, args.dry_run, label="main")

    append_path = APPEND_YAML
    if os.path.isfile(append_path):
        append_data = load_yaml(append_path)
        if selected_pkgs:
            append_data = {k: v for k, v in append_data.items() if k in selected_pkgs}
        if append_data:
            print(f"\n[sysroot-fix] Merging append file: {append_path}")
            a_matched, a_total, a_errors = apply_rules(append_data, sysroot, ros_distro, args.dry_run, label="append")
            matched += a_matched
            total += a_total
            errors += a_errors

    print(f"\n[sysroot-fix] Done. matched_rules={matched}/{total} errors={errors}")
    return 1 if errors else 0

if __name__ == "__main__":
    sys.exit(main())
