# Handoff: Jetson Orin Cross-Dev (from Renesas RDK reverse engineering)

**Audience:** Claude / coding agent continuing this work  
**Date:** 2026-10-26 (session), refreshed for handoff 2026-11-02  
**Owner machine:** Linux (CachyOS), user `samuela`, x86_64 workstation with Docker  

---

## What we are trying to achieve

Build a **ROS 2 cross-development environment** on an **x86_64 workstation** that:

1. Cross-compiles ROS 2 packages for **NVIDIA Jetson Orin** (Nano / NX / AGX).
2. Deploys, debugs, and traces those builds on **multiple boards** over the network.
3. Mirrors the proven **Renesas RZ/V2H RDK** workflow (`setup_rdk_docker.sh` + Docker + sysroot + VS Code remote GDB), but is **optimized for Jetson**:
   - JetPack CUDA / TensorRT / cuDNN / VPI / ONNX / inference stack in the **sysroot**
   - Remote **Nsight Systems / Nsight Compute**, `tegrastats`, and kernel/ftrace traces
   - Multi-board inventory (select / deploy-one / deploy-all)
4. Supports **ROS 2 Kilted** and **ROS 2 Lyrical Luth** as host image profiles.

**End state:** a reproducible Docker-based cross-dev kit (scripts + Dockerfiles + toolchains + docs) that a developer can point at several Orin boards and use daily for build → deploy → GDB → Nsight, especially for ROS 2 + object detection / AI inference workloads.

This is **not** “decompile a Docker image with Ghidra.” The Renesas image is mostly open scripts + a board rootfs. The Jetson kit should be a **clean reconstruction / fork of that workflow**, not a binary reverse of NVIDIA blobs.

---

## Context (how we got here)

### 1. REA + Cursor setup (prerequisite tooling)

- Project / tool: **REA** (`rea-agents`) — local reverse-engineering MCP for Cursor.
- Workspace: `/home/samuela/Projekte/rea`
- Done in session:
  - Installed/pinned Node via `fnm` to **24.22.1** (system Node 26.4.0 was outside REA engines).
  - Ran `npx rea-agents@latest setup --client cursor --yes`
  - Registered MCP in `~/.cursor/mcp.json` as `npx -y rea-agents@6.8.0 mcp`
  - Installed skill `~/.agents/skills/reverse-engineer-anything` (v49)
  - Installed **Ghidra 12.1.4** to `/home/samuela/tools/ghidra_12.1.4_PUBLIC` and wired:
    - `GHIDRA_INSTALL_DIR`
    - `JAVA_HOME=/usr/lib/jvm/java-21-openjdk`
  - Doctor reported Ghidra available.

**Note for agents:** REA does **not** install Ghidra/Java; those are bring-your-own. REA setup never installs unrelated system packages.

### 2. Renesas RDK reverse engineering (pattern source)

**Docs:**  
https://renesas-rdk.github.io/rzv2h_rdk_documentation/latest/chapter-4/development_guide/cross_build_setup.html

**Local artifacts:**
- Setup script: `/home/samuela/Projekte/setup_rdk_docker.sh`
- Image: `ghcr.io/renesas-rdk/rzv2h_ubuntu_xbuild:multiarch`
- Running container (at time of RE): `ros2_cross_build_container`
- Host WS mount observed: `/home/samuela/Projekte/HochschuleMünchen/ros2` → `/home/ubuntu/ros2_ws`
- Extracted evidence cache: `/home/samuela/Projekte/rea/.cache/rdk-re/` (toolchains, vscode scripts, image inspect, etc.)

**How Renesas cross-dev works (summary):**
- Host amd64 Ubuntu container, user `ubuntu`, privileged.
- Entrypoint: `/usr/local/bin/entrypoint` → git-managed repo `ubuntu_xbuild_toolchains` at `/home/ubuntu/toolchains`.
- Board sysroot: `/opt/arm64_sysroot` (from baked `ubuntu_rootfs_rzv2h.tar.bz2`).
- Build: `cross-colcon-build` forces CMake toolchain (`v2h_cross.cmake` / `PRODUCT=V2H`, Cortex-A55).
- Target package ops: `arm64-chroot` (QEMU + chroot) for apt/rosdep against sysroot.
- Deploy/debug: `.vscode/deploy.sh` (sshpass + rsync), `start_target_gdbserver.sh`, `gdb-multiarch` attach.
- Also ships Renesas-specific **DRP-AI TVM** — **do not** carry that into Jetson; replace with CUDA/TensorRT/etc.

Upstream toolchain repo (open source):  
https://github.com/renesas-rdk/ubuntu_xbuild_toolchains

### 3. Jetson project scaffold (main deliverable so far)

**Path:** `/home/samuela/Projekte/jetson-orin-xbuild`

Scaffold created with:
- RDK-like launcher `setup_jetson_docker.sh`
- Dual Dockerfiles: `kilted` (Ubuntu 24.04) and `lyrical` (Ubuntu 26.04)
- Orin CMake toolchains (`orin_nano` / `orin_nx` / `orin_agx`)
- Multi-board CLI `toolchains/scripts/board`
- Sysroot capture helper `jetson_sysroot_sync.sh`
- VS Code tasks extended for deploy-all / Nsight / tegrastats
- Docs under `docs/`

A local `git init` was started in that directory; **no requirement that it was committed/pushed**.

---

## Tasks already completed

### REA / Cursor
- [x] Diagnose Node engine mismatch; use Node 24.22.1 for REA
- [x] Register REA MCP + skill for Cursor
- [x] Install Ghidra 12.1.4 + wire into Cursor MCP env
- [x] Confirm REA tools appear (`user-rea` namespace)

### Renesas analysis
- [x] Locate and read `setup_rdk_docker.sh`
- [x] Inspect local image + running container (entrypoint, env, mounts)
- [x] Copy/analyze toolchains (`entrypoint.sh`, `cross-colcon-build.sh`, `arm64-chroot.sh`, cmake, sysroot-fix)
- [x] Map deploy/debug VS Code flow (settings, tasks, deploy.sh, gdbserver)
- [x] Document that Ghidra is largely unnecessary for understanding this workflow (scripts + rootfs)

### Jetson scaffold
- [x] Create `/home/samuela/Projekte/jetson-orin-xbuild` tree
- [x] Write `README.md`, `boards.example.yaml`, `.gitignore`
- [x] Write `setup_jetson_docker.sh` (profile kilted/lyrical, `--sysroot`, `--boards`, `--build`, docker.sock, privileged, toolchain overlay mount)
- [x] Write `docker/Dockerfile.kilted` and `Dockerfile.lyrical` + setup helpers
- [x] Write Orin cmake toolchains + adapted entrypoint / cross-colcon / arm64-chroot / sysroot-fix
- [x] Write multi-board `board` CLI (list/select/deploy/ssh/nsys/ncu/tegra-stats/kernel-trace)
- [x] Write `jetson_sysroot_sync.sh`
- [x] Extend `.vscode` settings/tasks for multi-board + Nsight buttons
- [x] Write docs: architecture, compatibility, sysroot, multi-board, nvidia-tracing
- [x] Syntax-check main bash scripts; JSON-validate tasks/settings template

---

## Issues / constraints discovered

1. **Sysroot ABI is the hard constraint**  
   - JetPack 6.2 ⇒ Ubuntu **22.04** ⇒ native apt ROS is typically **Humble**.  
   - ROS 2 **Kilted** ⇒ Ubuntu **24.04**.  
   - ROS 2 **Lyrical Luth** ⇒ Tier-1 Ubuntu **26.04** (released ~May 2026).  
   Cross-linking kilted/lyrical binaries against a JP6.2 sysroot is **ABI-unsafe**. Prefer JP7.x (24.04) sysroots for kilted; lyrical needs matching board OS or **source-built** ROS in the sysroot.

2. **NVIDIA libraries live in the board sysroot, not the host image**  
   CUDA / TensorRT / cuDNN / VPI / ONNX Runtime must be captured from a real JetPack install (or NVIDIA cross-compile container/sysroot). The amd64 Docker image only provides cross GCC, QEMU, gdb-multiarch, colcon, SSH/rsync tooling.

3. **No Jetson sysroot was captured yet**  
   `sysroots/` is empty aside from a README. Without `--sysroot`, cross-link against CUDA/TRT will fail.

4. **Docker images were not built/verified in-session**  
   `Dockerfile.kilted` / `Dockerfile.lyrical` are written but not proven to build on this host. Possible failure points:
   - ROS apt package names for `kilted` / `lyrical` on that Ubuntu codename
   - `ubuntu:26.04` availability on the local registry/mirror
   - `nsight-systems` / `nsight-compute` apt packages may be missing (install is best-effort / soft-fail)
   - `yq` package name on Ubuntu may differ; `python3-yaml` + `jq` are the real deps for `board`

5. **`board` CLI depends on PyYAML + jq**  
   Inventory is YAML. Ensure `python3-yaml` and `jq` are installed in the image (intended in `setup-base.sh`).

6. **Security defaults**  
   Renesas flow uses **sshpass + plaintext password** in settings. Jetson scaffold prefers empty password + SSH keys; password remains optional for compatibility. Do not commit real `boards.yaml` credentials.

7. **Renesas DRP-AI TVM is not portable**  
   Do not copy `/drp-ai_tvm` into the Jetson image. Replace with JetPack AI stack in sysroot.

8. **REA workspace vs Jetson project**  
   Jetson kit lives **outside** the REA git repo (`/home/samuela/Projekte/jetson-orin-xbuild`). Do not dump this product into `rea/` unless the user asks.

9. **Multi-board GDB ports**  
   Each board should use a distinct `gdb_port` in `boards.yaml` if debugging multiple targets concurrently.

10. **Partial automation in `board gdbserver`**  
    GDB server wrapper still leans on the Renesas-style `.vscode/start_target_gdbserver.sh` argument order; validate/fix when first used with `board gdbserver`.

---

## Open TODOs (prioritized)

### P0 — make it runnable
1. Copy `boards.example.yaml` → `boards.yaml` and fill real Orin IPs / products / users.
2. Prepare at least one Orin with JetPack (**prefer 7.x for kilted**) + ROS + CUDA/TRT/vision deps.
3. Capture sysroot:
   ```bash
   ./toolchains/scripts/jetson_sysroot_sync.sh \
     --board <IP> --dest ./sysroots/jp72-orin-agx
   ```
4. Build and start kilted container:
   ```bash
   ./setup_jetson_docker.sh kilted \
     -n jetson_cross_kilted \
     -w ~/ros2_ws_jetson \
     --sysroot "$(pwd)/sysroots/jp72-orin-agx" \
     --boards "$(pwd)/boards.yaml" \
     --product ORIN_AGX \
     --build -y --create --shell
   ```
5. Smoke-test: `arm64-chroot uname -m`, `cross-colcon-build --help`, `board list`, deploy a tiny package.

### P1 — AI / inference completeness
6. Verify sysroot contains: `/usr/local/cuda`, TensorRT libs, cuDNN, VPI, and any ONNX Runtime install; document missing pieces.
7. Add example ROS 2 package (e.g. TensorRT or ONNX object-detection node) that **finds** CUDA/TRT via the Orin cmake toolchain.
8. Prove `board nsys` end-to-end (requires `nsys` on board); pull `.nsys-rep` into workspace.
9. Optionally wire host Nsight GUI instructions if apt packages are absent in the container.

### P1 — multi-board polish
10. Test `board deploy --all` against ≥2 boards.
11. Test concurrent debug ports / `ROS_DOMAIN_ID` overrides per board.
12. Harden `board gdbserver` argument plumbing vs `start_target_gdbserver.sh`.
13. Prefer SSH keys; document `ssh-copy-id` setup; keep sshpass as fallback only.

### P2 — lyrical profile
14. Confirm `ubuntu:26.04` pull works; fix Dockerfile if tag/mirror differs.
15. Confirm `ros-lyrical-*` apt packages install on that base; otherwise document source-build path.
16. Pair lyrical host image only with a compatible board/sysroot story (see `docs/compatibility.md`).

### P2 — product quality
17. CI: shellcheck + `bash -n` + docker build dry-run for kilted.
18. Pin toolchain overlay behavior (bind-mount of `./toolchains` is intentional for live edit).
19. Publish/push repo if user wants a remote; currently local under Projekte.
20. Optional: NVIDIA NGC JetPack cross-compile container as an alternative/base for CUDA cross tools — evaluate vs current GCC + board sysroot approach.

### Explicit non-goals (unless user reopens)
- Ghidra-decompiling the Renesas or Jetson Docker images as a whole
- Shipping a full Jetson rootfs inside git
- Installing Hopper/IDA for this workflow
- Supporting JetPack 6.2 + kilted/lyrical without a clear source-ROS-in-sysroot plan

---

## Key paths cheat sheet

| Path | Meaning |
|------|---------|
| `/home/samuela/Projekte/jetson-orin-xbuild` | **Main project to continue** |
| `/home/samuela/Projekte/jetson-orin-xbuild/setup_jetson_docker.sh` | Host launcher |
| `/home/samuela/Projekte/jetson-orin-xbuild/toolchains/scripts/board` | Multi-board CLI |
| `/home/samuela/Projekte/jetson-orin-xbuild/docs/` | Architecture + compatibility |
| `/home/samuela/Projekte/setup_rdk_docker.sh` | Renesas original launcher |
| `/home/samuela/Projekte/rea/.cache/rdk-re/` | Extracted Renesas evidence |
| `~/.cursor/mcp.json` | REA MCP registration |
| `/home/samuela/tools/ghidra_12.1.4_PUBLIC` | Ghidra install |

---

## Suggested first commands for the next agent

```bash
cd /home/samuela/Projekte/jetson-orin-xbuild
ls -la
cat README.md docs/compatibility.md docs/architecture.md
# Then either:
#  A) Fix/build Dockerfile.kilted and report apt/ROS failures, or
#  B) If boards are reachable, capture a sysroot and run setup_jetson_docker.sh --build
```

Ask the user for:
- JetPack version on their Orins (6.2 vs 7.x)
- Whether boards are on the LAN with SSH
- Whether to prioritize **kilted** first (recommended) before **lyrical**
- Board inventory details for `boards.yaml`

---

## Success criteria

- [ ] `jetson-orin-xbuild:kilted` image builds cleanly on the workstation  
- [ ] Container starts with mounted JetPack sysroot + workspace + boards.yaml  
- [ ] `cross-colcon-build` produces aarch64 binaries linked against sysroot CUDA/TRT (or clearly documents missing libs)  
- [ ] `board deploy` / `board deploy --all` sync `install/` to Orins  
- [ ] Remote GDB attach works via gdb-multiarch + board gdbserver  
- [ ] At least one Nsight Systems capture round-trips to the host workspace  
- [ ] Docs state a coherent Kilted vs Lyrical vs JetPack pairing without ABI lies  

---

## One-sentence mission

**Replicate the Renesas ROS 2 Docker cross-build/deploy/debug workflow for NVIDIA Jetson Orin on an x86 workstation, with multi-board operations and first-class JetPack AI + Nsight tracing — starting from the scaffold in `/home/samuela/Projekte/jetson-orin-xbuild` and making kilted + a real JP7 sysroot actually work end-to-end.**
