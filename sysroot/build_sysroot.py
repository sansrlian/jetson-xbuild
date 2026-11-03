#!/usr/bin/env python3
"""Build an aarch64 Jetson sysroot from the board's own apt repositories, without emulation.

apt resolves the package closure for arm64 against a private, empty dpkg database. The .debs are then
only *extracted* (no maintainer scripts, no QEMU). The few side effects of maintainer scripts that matter
for compiling and linking are reproduced afterwards: the dpkg status database, update-alternatives links,
merged-/usr links and relative symlinks. The result matches what the board installs from the same repos.

Runs inside the jxb xbuild container (needs apt-get, dpkg-deb, tar). Usually called through `jxb sysroot build`.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
from pathlib import Path

JXB_HOME = Path(os.environ.get("JXB_HOME", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(JXB_HOME / "toolchain"))
from jxb_profile import load_profile  # noqa: E402
INST_RE = re.compile(r"^Inst (\S+) (?:\[\S+\] )?\((\S+) .*\[(\S+)\]\)")


def log(msg: str) -> None:
    print(f"[sysroot] {msg}", flush=True)


def die(msg: str) -> None:
    print(f"[sysroot] ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, **kw)


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    log(f"download {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    if url.startswith("file://"):        # locally built artifacts, e.g. extras/onnxruntime/build.sh
        shutil.copy(url[7:], tmp)
        tmp.rename(dest)
        return dest
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, length=1 << 20)
    tmp.rename(dest)
    return dest


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------------------------- apt
class PrivateApt:
    """apt-get with its own config, lists, cache and an empty dpkg status, treating arm64 as native."""

    def __init__(self, root: Path, profile: dict, keys: dict[str, Path], debs: Path):
        self.root, self.debs = root, debs
        self.conf = root / "apt.conf"
        (debs / "partial").mkdir(parents=True, exist_ok=True)
        sr = profile["sysroot"]
        for d in ("etc/apt/apt.conf.d", "etc/apt/preferences.d", "etc/apt/sources.list.d",
                  "etc/apt/trusted.gpg.d", "var/lib/apt/lists/partial", "var/cache/apt/archives/partial",
                  "var/lib/dpkg", "var/log/apt"):
            (root / d).mkdir(parents=True, exist_ok=True)
        (root / "var/lib/dpkg/status").write_text("")
        subst = {f"{k}_key": str(v) for k, v in keys.items()}
        lines = [s.format(**subst) for s in sr["sources"]]
        (root / "etc/apt/sources.list").write_text("\n".join(lines) + "\n")
        (root / "etc/apt/preferences").write_text(sr.get("preferences", "").strip() + "\n")
        arch = sr.get("arch", "arm64")
        self.conf.write_text(f"""
Dir "{root}/";
Dir::State::status "{root}/var/lib/dpkg/status";
Dir::Etc::Parts "{root}/etc/apt/apt.conf.d";
Dir::Cache::Archives "{debs}/";
APT::Architecture "{arch}";
APT::Architectures {{ "{arch}"; }};
APT::Install-Recommends "false";
APT::Install-Suggests "false";
APT::Get::Assume-Yes "true";
Acquire::Languages "none";
Acquire::Retries "3";
Debug::NoLocking "true";
""")
        self.env = dict(os.environ, APT_CONFIG=str(self.conf), DEBIAN_FRONTEND="noninteractive")

    def apt(self, *args: str, capture: bool = False) -> str:
        res = subprocess.run(["apt-get", *args], env=self.env, text=True,
                             stdout=subprocess.PIPE if capture else None)
        if res.returncode != 0:
            die(f"apt-get {' '.join(args[:3])} ... failed (exit {res.returncode})")
        return res.stdout or ""

    def resolve(self, packages: list[str]) -> list[tuple[str, str, str]]:
        out = self.apt("-s", "install", *packages, capture=True)
        sel = [m.groups() for m in map(INST_RE.match, out.splitlines()) if m]
        if not sel:
            die("apt resolved no packages; check the package sets")
        return sel

    def unavailable(self, packages: list[str]) -> list[str]:
        """Packages (name or name=version) without an install candidate in the configured repos."""
        names = [p.split("=", 1)[0] for p in packages]
        out = subprocess.run(["apt-cache", "policy", *names], env=self.env, text=True, capture_output=True).stdout
        have = {m.group(1) for m in re.finditer(r"^(\S+):\n\s+Installed: .*\n\s+Candidate: (?!\(none\))", out, re.M)}
        return [p for p in packages if p.split("=", 1)[0] not in have]

    def deb_path(self, name: str, version: str, arch: str) -> Path:
        fname = f"{name}_{version.replace(':', '%3a')}_{arch}.deb"
        return self.debs / fname


# --------------------------------------------------------------------------------------- packages
def read_sets(names: list[str], distro: str) -> list[str]:
    pkgs: list[str] = []
    for name in names:
        path = JXB_HOME / "sysroot/sets" / f"{name}.txt"
        if not path.exists():
            die(f"package set not found: {path}")
        for line in path.read_text().splitlines():
            line = line.split("#", 1)[0].strip().replace("{ros_distro}", distro)
            if line:
                pkgs.append(line)
    return list(dict.fromkeys(pkgs))


def extract_deb(deb: Path, dest: Path, scripts_dir: Path) -> str:
    """Extract data + control of one .deb; returns the control paragraph."""
    data = subprocess.Popen(["dpkg-deb", "--fsys-tarfile", str(deb)], stdout=subprocess.PIPE)
    tar = subprocess.run(["tar", "-x", "-C", str(dest), "--keep-directory-symlink", "--no-same-owner",
                          "--warning=no-timestamp", "-f", "-"], stdin=data.stdout, capture_output=True, text=True)
    data.stdout.close()
    if data.wait() != 0 or tar.returncode != 0:
        raise RuntimeError(f"extract failed: {deb.name}: {tar.stderr.strip()[:500]}")
    ctrl_dir = scripts_dir / deb.name
    ctrl_dir.mkdir(parents=True, exist_ok=True)
    run(["dpkg-deb", "-e", str(deb), str(ctrl_dir)])
    return (ctrl_dir / "control").read_text()


# ------------------------------------------------------------------------------- post-processing
def ensure_merged_usr(root: Path) -> None:
    for name in ("bin", "sbin", "lib"):
        p = root / name
        if not p.exists() and not p.is_symlink():
            (root / "usr" / name).mkdir(parents=True, exist_ok=True)
            p.symlink_to(f"usr/{name}")


def parse_alternatives(script: str) -> list[tuple[str, str, str, int, list[tuple[str, str, str]]]]:
    """Find literal `update-alternatives --install link name path prio [--slave link name path]...`."""
    found = []
    for line in script.replace("\\\n", " ").splitlines():
        if "update-alternatives" not in line or "--install" not in line:
            continue
        try:
            toks = shlex.split(line, comments=True)
        except ValueError:
            continue
        for i, tok in enumerate(toks):
            if tok != "--install" or i + 4 >= len(toks):
                continue
            link, name, path, prio = toks[i + 1:i + 5]
            if "$" in link + name + path or not prio.lstrip("-").isdigit():
                continue
            slaves, j = [], i + 5
            while j < len(toks) and toks[j] != "--install":
                if toks[j] == "--slave" and j + 3 < len(toks):
                    s = toks[j + 1:j + 4]
                    if "$" not in "".join(s):
                        slaves.append(tuple(s))
                    j += 4
                else:
                    j += 1
            found.append((link, name, path, int(prio), slaves))
    return found


def rel_symlink(root: Path, link: str, target: str) -> None:
    """Create root/link -> target (target given as absolute board path), as a relative symlink."""
    lp = root / link.lstrip("/")
    lp.parent.mkdir(parents=True, exist_ok=True)
    # relative to the real parent: /lib/cpp physically lives in usr/lib because lib -> usr/lib
    parent = lp.parent.resolve()
    lp = parent / lp.name
    rel = os.path.relpath(root.resolve() / target.lstrip("/"), parent)
    if lp.is_symlink() or lp.exists():
        if lp.is_dir() and not lp.is_symlink():
            return
        lp.unlink()
    lp.symlink_to(rel)


def apply_alternatives(root: Path, scripts_dir: Path) -> list[str]:
    best: dict[str, tuple] = {}
    for postinst in scripts_dir.glob("*/postinst"):
        for alt in parse_alternatives(postinst.read_text(errors="replace")):
            link, name, path, prio, slaves = alt
            if not (root / path.lstrip("/")).exists():
                continue
            if name not in best or prio > best[name][3]:
                best[name] = alt
    done = []
    for name, (link, _, path, prio, slaves) in sorted(best.items()):
        rel_symlink(root, f"/etc/alternatives/{name}", path)
        rel_symlink(root, link, f"/etc/alternatives/{name}")
        for s_link, s_name, s_path in slaves:
            if (root / s_path.lstrip("/")).exists():
                rel_symlink(root, f"/etc/alternatives/{s_name}", s_path)
                rel_symlink(root, s_link, f"/etc/alternatives/{s_name}")
        done.append(f"{name}: {link} -> {path} ({prio})")
    return done


ASSIGN_RE = re.compile(r"""^\s*([A-Za-z_]\w*)=(?:"([^"$`]*)"|'([^']*)'|([^\s;$`"']*))\s*$""")
VAR_RE = re.compile(r"\$\{(\w+)(?::[?-][^}]*)?\}|\$(\w+)")


def apply_postinst_links(root: Path, scripts_dir: Path) -> list[str]:
    """Replay `ln -s SRC DEST` from postinst scripts; literal variable assignments in the script are expanded."""
    done = []
    for postinst in scripts_dir.glob("*/postinst"):
        variables: dict[str, str] = {}
        for line in postinst.read_text(errors="replace").replace("\\\n", " ").splitlines():
            m = ASSIGN_RE.match(line)
            if m:
                variables[m.group(1)] = next(g for g in m.groups()[1:] if g is not None)
                continue
            if not re.match(r"^\s*ln\s", line):
                continue
            try:
                toks = shlex.split(line, comments=True)
            except ValueError:
                continue
            flags = "".join(t[1:] for t in toks[1:] if t.startswith("-") and not t.startswith("--"))
            args = [t for t in toks[1:] if not t.startswith("-")]
            if "s" not in flags or len(args) != 2:
                continue
            unknown = []
            expand = lambda s: VAR_RE.sub(lambda v: variables.get(v.group(1) or v.group(2)) or unknown.append(v) or "", s)
            src, dest = (os.path.normpath(expand(a)) for a in args)
            if unknown or not src.startswith("/") or not dest.startswith("/") or not (root / src.lstrip("/")).exists():
                continue
            if (root / dest.lstrip("/")).is_dir() and not (root / dest.lstrip("/")).is_symlink():
                dest = f"{dest}/{os.path.basename(src)}"
            rel_symlink(root, dest, src)
            done.append(f"{dest} -> {src}")
    return done


def apply_gpu_variant(root: Path, variant: str) -> list[str]:
    """Replay the board's boot-time nv-load-gpu-libs.sh (nvidia-l4t-init) for the profile's GPU driver variant.

    JetPack 7.2 ships the CUDA driver per variant under /opt/nvidia/l4t-gpu-libs/{nvgpu,openrm}; at boot the
    board publishes the active one via ld.so.conf.d and libcuda.so{,.1} links. Orin uses nvgpu, Thor openrm.
    """
    lib_dir = f"/opt/nvidia/l4t-gpu-libs/{variant}"
    if not (root / lib_dir.lstrip("/")).is_dir():
        return []
    conf = root / "etc/ld.so.conf.d/000-nvidia-gpu-libs.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(lib_dir + "\n")
    for name in ("libcuda.so", "libcuda.so.1"):
        rel_symlink(root, f"/usr/lib/aarch64-linux-gnu/{name}", f"{lib_dir}/{name}")
    return [str(conf.relative_to(root)), "usr/lib/aarch64-linux-gnu/libcuda.so{,.1}"]


def relativize_symlinks(root: Path) -> int:
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        for n in dirnames + filenames:
            p = Path(dirpath, n)
            if not p.is_symlink():
                continue
            target = os.readlink(p)
            if target.startswith("/"):
                p.unlink()
                p.symlink_to(os.path.relpath(root / target.lstrip("/"), p.parent))
                count += 1
    return count


def install_extras(root: Path, profile: dict, cache: Path, scripts: Path) -> tuple[dict, list[str]]:
    """Non-apt artifacts: release tarballs (onnxruntime) and .debs by URL (e.g. CV-CUDA)."""
    info, controls = {}, []
    for name in profile["sysroot"].get("extras", []):
        cfg = profile.get("extras", {}).get(name)
        if cfg is None:
            die(f"extra '{name}' has no [extras.{name}] section in the profile")
        fmt = {**cfg, "profile": profile["name"], "jetpack": profile["board"]["jetpack"], "jxb_home": str(JXB_HOME),
               "ros_distro": profile["board"]["ros_distro"]}
        if "tarball" in cfg:
            url = cfg["tarball"].format(**fmt)
            if url.startswith("file://") and not Path(url[7:]).exists():
                if cfg.get("optional"):
                    log(f"extra '{name}' skipped: {url[7:]} not built yet ({cfg.get('build', '')})")
                    continue
                die(f"extra '{name}': {url[7:]} missing")
            tgz = download(url, cache / "extras" / Path(url).name)
            dest = root / cfg["dest"].format(**fmt).lstrip("/")
            dest.mkdir(parents=True, exist_ok=True)
            strip = int(cfg.get("strip", 1))
            with tarfile.open(tgz) as t:
                members = []
                for m in t.getmembers():
                    parts = m.name.split("/", strip)
                    if len(parts) > strip and parts[strip]:
                        m.name = parts[strip]
                        members.append(m)
                t.extractall(dest, members=members, filter="tar")
            info[name] = {"url": url, "sha256": sha256(tgz), "dest": cfg["dest"].format(**fmt)}
        elif "debs" in cfg:
            info[name] = []
            for url in cfg["debs"]:
                url = url.format(**cfg)
                deb = download(url, cache / "extras" / Path(url).name)
                controls.append(extract_deb(deb, root, scripts))
                info[name].append({"url": url, "sha256": sha256(deb)})
        elif name == "onnxruntime":
            url = cfg["url"].format(**fmt)
            local = cfg.get("local_build", "").format(**fmt)
            if local.startswith("file://") and Path(local[7:]).exists():
                url = local          # jxb's own GPU build (CUDA + TensorRT EP) wins over the CPU release
            tgz = download(url, cache / "extras" / Path(url).name)
            dest = root / "opt/onnxruntime"
            dest.mkdir(parents=True, exist_ok=True)
            with tarfile.open(tgz) as t:
                members = []
                for m in t.getmembers():
                    parts = m.name.split("/", 1)
                    if len(parts) == 2 and parts[1]:
                        m.name = parts[1]
                        members.append(m)
                t.extractall(dest, members=members, filter="tar")
            # The release's onnxruntimeConfig.cmake expects lib64/ and include/onnxruntime/; the tarball has
            # lib/ and a flat include/. Add the expected paths so find_package(onnxruntime) works too.
            if not (dest / "lib64").exists():
                (dest / "lib64").symlink_to("lib")
            inc = dest / "include"
            if not (inc / "onnxruntime").exists():
                (inc / "onnxruntime").mkdir()
                for entry in inc.iterdir():
                    if entry.name != "onnxruntime":
                        (inc / "onnxruntime" / entry.name).symlink_to(f"../{entry.name}")
            info["onnxruntime"] = {"version": cfg["version"], "url": url, "sha256": sha256(tgz)}
            if cfg.get("cuda_ep_url"):
                ep_url = cfg["cuda_ep_url"].format(**cfg)
                ep = download(ep_url, cache / "extras" / Path(ep_url).name)
                ep_dest = root / "opt/onnxruntime-plugins/cuda"
                ep_dest.mkdir(parents=True, exist_ok=True)
                with tarfile.open(ep) as t:
                    t.extractall(ep_dest, filter="tar")
                info["onnxruntime_cuda_ep"] = {"version": cfg["cuda_ep_version"], "url": ep_url,
                                               "sha256": sha256(ep)}
        else:
            die(f"unknown extra '{name}'")
    return info, controls


def apply_fixups(root: Path, profile: dict) -> list[str]:
    """Patch hard-coded absolute paths in exported CMake files (rules in sysroot/fixups.toml)."""
    rules_file = JXB_HOME / "sysroot/fixups.toml"
    if not rules_file.exists():
        return []
    rules = tomllib.loads(rules_file.read_text()).get("rule", [])
    distro = profile["board"]["ros_distro"]
    applied = []
    for r in rules:
        target = root / r["file"].replace("{ros_distro}", distro).lstrip("/")
        if not target.exists():
            continue
        text = target.read_text()
        find = r["find"].replace("{ros_distro}", distro)
        if find in text:
            target.write_text(text.replace(find, r["replace"]))
            applied.append(str(target.relative_to(root)))
    return applied


# ------------------------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True, help="profile TOML")
    ap.add_argument("--out", required=True, help="sysroot directory to (re)create")
    ap.add_argument("--cache", required=True, help="per-profile cache (apt lists, extras)")
    ap.add_argument("--deb-cache", help=".deb cache, shareable between profiles (default: <cache>/debs)")
    ap.add_argument("--add", nargs="*", default=[], help="extra packages on top of the profile's sets")
    ap.add_argument("--extra-sets", nargs="*", default=[], help="extra package sets (sysroot/sets/<name>.txt)")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--no-update", action="store_true", help="reuse the cached apt lists")
    args = ap.parse_args()

    profile = load_profile(Path(args.profile))
    out, cache = Path(args.out).resolve(), Path(args.cache).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now(dt.timezone.utc)

    keys = {}
    for name, ref in profile.get("keys", {}).items():
        if ref.startswith("http"):
            # apt's signed-by decides by extension: .asc = ASCII-armored, .gpg = binary keyring
            raw = download(ref, cache / "keys" / f"{name}.key")
            ext = ".asc" if raw.read_bytes()[:64].lstrip().startswith(b"-----BEGIN") else ".gpg"
            keys[name] = raw.with_suffix(ext)
            shutil.copy(raw, keys[name])
        else:
            keys[name] = Path(ref)
        if not keys[name].exists():
            die(f"key '{name}' not found: {keys[name]}")

    apt = PrivateApt(cache / "apt", profile, keys, Path(args.deb_cache or cache / "debs").resolve())
    if not args.no_update:
        log("apt-get update (arm64)")
        apt.apt("update")

    distro = profile["board"]["ros_distro"]
    wanted = read_sets(profile["sysroot"]["package_sets"], distro) + args.add
    # Workspace sets (rosdep output) may name packages the board's repos don't have, e.g. depthai for Lyrical on
    # noble: leave those out with a warning instead of failing the whole sysroot.
    extra = [p for p in read_sets(args.extra_sets, distro) if p not in wanted]
    missing = apt.unavailable(extra) if extra else []
    if missing:
        log(f"WARNING: not in the board's repos, left out: {' '.join(missing)}")
    wanted = list(dict.fromkeys(wanted + [p for p in extra if p not in missing]))
    log(f"resolving {len(wanted)} requested packages")
    selection = apt.resolve(wanted)
    log(f"closure: {len(selection)} packages; downloading")
    apt.apt("install", "--download-only", *wanted)

    staging = out.with_name(out.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    ensure_merged_usr(staging)
    scripts = Path(tempfile.mkdtemp(prefix="jxb-ctrl-", dir=cache))

    debs = []
    for name, version, arch in selection:
        deb = apt.deb_path(name, version, arch)
        if not deb.exists():
            die(f"missing download: {deb.name}")
        debs.append(deb)
    log(f"extracting {len(debs)} packages with {args.jobs} jobs")
    controls: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        for ctrl in pool.map(lambda d: extract_deb(d, staging, scripts), debs):
            controls.append(ctrl)
    extras, extra_controls = install_extras(staging, profile, cache, scripts)
    controls += extra_controls

    dpkg = staging / "var/lib/dpkg"
    (dpkg / "info").mkdir(parents=True, exist_ok=True)
    with open(dpkg / "status", "w") as st:
        for ctrl in controls:
            lines = ctrl.strip().splitlines()
            st.write(lines[0] + "\nStatus: install ok installed\n" + "\n".join(lines[1:]) + "\n\n")
    alts = apply_alternatives(staging, scripts)
    links = apply_postinst_links(staging, scripts)
    links += apply_gpu_variant(staging, profile["board"].get("gpu_variant", "nvgpu"))
    log(f"update-alternatives: {len(alts)} links; postinst ln -s: {len(links)} links")

    cuda = profile["image"]["cuda_version"]
    cuda_target = profile["sysroot"].get("cuda_target", "sbsa-linux")
    cuda_dir = staging / f"usr/local/cuda-{cuda}"
    if (cuda_dir / "targets" / cuda_target).is_dir():
        # CMake < 4 only knows targets/aarch64-linux for aarch64 cross builds
        alias = cuda_dir / "targets/aarch64-linux"
        if not alias.exists():
            alias.symlink_to(cuda_target)
        if not (staging / "usr/local/cuda").exists():
            (staging / "usr/local/cuda").symlink_to(f"cuda-{cuda}")
    fixed = apply_fixups(staging, profile)
    n_rel = relativize_symlinks(staging)
    log(f"relativized {n_rel} absolute symlinks; {len(fixed)} CMake fixups")

    meta = staging / ".jxb"
    meta.mkdir()
    lock = {n: v for n, v, _ in selection}
    for ctrl in extra_controls:
        fields = dict(l.split(": ", 1) for l in ctrl.splitlines() if ": " in l and not l.startswith(" "))
        lock[fields["Package"]] = fields["Version"]
    (meta / "packages.lock").write_text("".join(f"{n}={v}\n" for n, v in sorted(lock.items())))
    (meta / "sources.list").write_text((apt.root / "etc/apt/sources.list").read_text())
    (meta / "info.json").write_text(json.dumps({
        "profile": profile["name"], "built": started.isoformat(timespec="seconds"),
        "packages": len(selection), "requested": wanted, "alternatives": alts, "postinst_links": links,
        "extras": extras, "fixups": fixed,
    }, indent=2) + "\n")
    (meta / "profile.json").write_text(json.dumps(profile, indent=2) + "\n")
    shutil.rmtree(scripts, ignore_errors=True)

    old = out.with_name(out.name + ".old")
    if out.exists():
        if old.exists():
            shutil.rmtree(old)
        out.rename(old)
    staging.rename(out)
    if old.exists():
        shutil.rmtree(old)
    size = run(["du", "-sh", str(out)], capture_output=True).stdout.split()[0]
    took = (dt.datetime.now(dt.timezone.utc) - started).seconds
    log(f"done: {out} ({len(selection)} packages, {size}, {took}s)")


if __name__ == "__main__":
    main()
