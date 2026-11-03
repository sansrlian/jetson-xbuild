#!/usr/bin/env bash
# setup_jetson_docker.sh — RDK-pattern launcher for Jetson Orin multi-board cross-dev.
set -euo pipefail

DEFAULT_IMAGE="jetson-orin-xbuild:kilted"
DEFAULT_CONTAINER_NAME="jetson_cross_build_container"
DEFAULT_ROS2_WS="$HOME/ros2_ws_jetson"
DEFAULT_SYSROOT=""
BOARDS_FILE=""
BUILD_IMAGE=0
DOCKER_PLATFORM="native"
CONTAINER_NAME=""
ROS2_WS=""
AUTO_YES=0
AUTO_PULL=0
AUTO_CREATE=0
AUTO_SHELL=0
PRODUCT="${PRODUCT:-ORIN_AGX}"
__INPUT_RESULT=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROFILE="${1:-}"
if [ "$PROFILE" = "kilted" ] || [ "$PROFILE" = "lyrical" ]; then
  DEFAULT_IMAGE="jetson-orin-xbuild:${PROFILE}"
  shift
elif [ "$PROFILE" = "jp72" ]; then
  DEFAULT_IMAGE="jetson-orin-xbuild:kilted"
  shift
fi
IMAGE="$DEFAULT_IMAGE"

usage() {
  cat <<EOF
Usage: $0 [profile] [options]

Profile:
  kilted     Ubuntu 24.04 + ROS 2 Kilted (default; pair with JetPack 7.x sysroot)
  lyrical    Ubuntu 26.04 + ROS 2 Lyrical Luth LTS
  jp72       Alias for kilted

Options:
  -i, --image IMAGE            Docker image
      --platform PLATFORM      native | linux/amd64 | linux/arm64 (default: native)
  -n, --container-name NAME    Container name
  -w, --workspace PATH         Host ROS 2 workspace
      --sysroot PATH           Host Jetson sysroot → /opt/arm64_sysroot
      --boards FILE            boards.yaml inventory (multi-board)
      --product PRODUCT        ORIN_NANO | ORIN_NX | ORIN_AGX (default: ORIN_AGX)
      --build                  Build image from ./docker if missing
  -y, --yes                    Non-interactive yes
      --pull                   Pull/update image when needed
      --create                 Create/start container when needed
      --shell                  Open a shell after setup
  -h, --help                   Show help
EOF
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Error: required command '$1' not found."; exit 1; }
}

ask_input() {
  local prompt="$1" default_value="$2" user_input
  read -r -p "$prompt [default: $default_value]: " user_input
  __INPUT_RESULT="${user_input:-$default_value}"
}

confirm_yes() {
  [ "$AUTO_YES" -eq 1 ] && return 0
  local reply
  read -r -p "$1 [Y/n]: " reply
  reply="${reply:-Y}"
  [[ "$reply" =~ ^[Yy]$ ]]
}

confirm_no_default() {
  [ "$AUTO_SHELL" -eq 1 ] && return 0
  [ "$AUTO_YES" -eq 1 ] && return 1
  local reply
  read -r -p "$1 [y/N]: " reply
  reply="${reply:-N}"
  [[ "$reply" =~ ^[Yy]$ ]]
}

_lexical_normalize() {
  local input="$1" result="" part oldifs="$IFS"
  set -f; IFS='/'; set -- $input; IFS="$oldifs"; set +f
  for part in "$@"; do
    case "$part" in ''|.) ;; ..) result="${result%/*}" ;; *) result="$result/$part" ;; esac
  done
  printf '%s\n' "${result:-/}"
}

resolve_path() {
  local path="$1"
  case "$path" in
    "~") path="$HOME" ;;
    "~"/*) path="$HOME/${path:2}" ;;
  esac
  [[ "$path" != /* ]] && path="$(pwd)/$path"
  if command -v realpath >/dev/null 2>&1; then
    realpath -m -- "$path" 2>/dev/null || { [ -e "$path" ] && realpath "$path" || _lexical_normalize "$path"; }
  else
    _lexical_normalize "$path"
  fi
}

validate_platform() {
  case "$DOCKER_PLATFORM" in native|linux/amd64|linux/arm64) ;; *)
    echo "Error: invalid --platform '$DOCKER_PLATFORM'" >&2; exit 1 ;;
  esac
}

docker_platform_args() {
  [ "$DOCKER_PLATFORM" != "native" ] && printf '%s\n' --platform "$DOCKER_PLATFORM"
}

is_numeric_gid() { case "$1" in ""|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }

get_docker_socket_gid() {
  local socket="$1" image="${2:-}" gid
  if [ -n "$image" ]; then
    gid="$(docker run --rm -v "$socket:$socket" --entrypoint stat "$image" -c %g "$socket" 2>/dev/null || true)"
    is_numeric_gid "$gid" && { echo "$gid"; return 0; }
  fi
  gid="$(stat -c %g "$socket" 2>/dev/null || true)"
  is_numeric_gid "$gid" && echo "$gid"
}

local_image_available() { docker image inspect "$IMAGE" >/dev/null 2>&1; }

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      -i|--image) IMAGE="$2"; shift 2 ;;
      --platform) DOCKER_PLATFORM="$2"; shift 2 ;;
      --platform=*) DOCKER_PLATFORM="${1#--platform=}"; shift ;;
      -n|--container-name) CONTAINER_NAME="$2"; shift 2 ;;
      -w|--workspace) ROS2_WS="$2"; shift 2 ;;
      --sysroot) DEFAULT_SYSROOT="$2"; shift 2 ;;
      --boards) BOARDS_FILE="$2"; shift 2 ;;
      --product) PRODUCT="$2"; shift 2 ;;
      --build) BUILD_IMAGE=1; shift ;;
      -y|--yes) AUTO_YES=1; shift ;;
      --pull) AUTO_PULL=1; shift ;;
      --create) AUTO_CREATE=1; shift ;;
      --shell) AUTO_SHELL=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) echo "Error: unknown option: $1"; usage; exit 1 ;;
    esac
  done
}

parse_args "$@"
validate_platform
require_cmd docker

echo "=============================================="
echo " Jetson Orin Docker Cross-Build Setup"
echo "=============================================="
echo

if [ -z "$CONTAINER_NAME" ]; then
  ask_input "Enter container name" "$DEFAULT_CONTAINER_NAME"
  CONTAINER_NAME="$__INPUT_RESULT"
fi

if docker ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER_NAME"; then
  echo "Container '$CONTAINER_NAME' already exists. Remove it or choose another name."
  echo "  docker rm -f $CONTAINER_NAME"
  exit 1
fi

if [ -z "$ROS2_WS" ]; then
  ask_input "Enter ROS 2 workspace path on host" "$DEFAULT_ROS2_WS"
  ROS2_WS_INPUT="$__INPUT_RESULT"
else
  ROS2_WS_INPUT="$ROS2_WS"
fi
ROS2_WS="$(resolve_path "$ROS2_WS_INPUT")"

echo
echo "Configuration:"
echo "  Docker image   : $IMAGE"
echo "  Docker platform: $DOCKER_PLATFORM"
echo "  Container name : $CONTAINER_NAME"
echo "  ROS 2 workspace: $ROS2_WS"
echo "  Sysroot        : ${DEFAULT_SYSROOT:-<none — pass --sysroot>}"
echo "  Boards file    : ${BOARDS_FILE:-<optional>}"
echo "  PRODUCT        : $PRODUCT"
echo

confirm_yes "Proceed with this configuration?" || { echo "Aborted."; exit 0; }
mkdir -p "$ROS2_WS"

PLATFORM_ARGS=()
while IFS= read -r arg; do PLATFORM_ARGS+=("$arg"); done < <(docker_platform_args)

SHOULD_PULL=0
if local_image_available; then
  echo "Local image exists."
else
  echo "Local image missing."
  SHOULD_PULL=1
fi

if [ "$SHOULD_PULL" -eq 1 ]; then
  tag="${IMAGE##*:}"
  if [ "$BUILD_IMAGE" -eq 1 ] || [ "$AUTO_PULL" -eq 1 ] || confirm_yes "Build image now from ./docker/Dockerfile.$tag ?"; then
    echo "==> Building $IMAGE ..."
    docker build "${PLATFORM_ARGS[@]+"${PLATFORM_ARGS[@]}"}" \
      -f "$SCRIPT_DIR/docker/Dockerfile.$tag" \
      -t "$IMAGE" \
      "$SCRIPT_DIR"
  else
    echo "Error: image unavailable. Re-run with --build."
    exit 1
  fi
fi

echo
if [ "$AUTO_CREATE" -eq 1 ] || confirm_yes "Create and start container now?"; then
  DOOD_ARGS=()
  if [ -S /var/run/docker.sock ]; then
    DOOD_ARGS=(-v /var/run/docker.sock:/var/run/docker.sock)
    gid="$(get_docker_socket_gid /var/run/docker.sock "$IMAGE" || true)"
    [ -n "${gid:-}" ] && DOOD_ARGS+=(--group-add "$gid")
  fi

  EXTRA_MOUNTS=()
  if [ -n "$DEFAULT_SYSROOT" ]; then
    SYSROOT_HOST="$(resolve_path "$DEFAULT_SYSROOT")"
    [ -d "$SYSROOT_HOST" ] || { echo "Error: sysroot missing: $SYSROOT_HOST" >&2; exit 1; }
    EXTRA_MOUNTS+=(-v "$SYSROOT_HOST:/opt/arm64_sysroot:rw")
  fi
  if [ -n "$BOARDS_FILE" ]; then
    EXTRA_MOUNTS+=(-v "$(resolve_path "$BOARDS_FILE"):/home/ubuntu/boards.yaml:ro")
  elif [ -f "$SCRIPT_DIR/boards.yaml" ]; then
    EXTRA_MOUNTS+=(-v "$SCRIPT_DIR/boards.yaml:/home/ubuntu/boards.yaml:ro")
  fi
  EXTRA_MOUNTS+=(-v "$SCRIPT_DIR/toolchains:/home/ubuntu/toolchains:rw")

  ros_distro="kilted"
  [[ "$IMAGE" == *:lyrical ]] && ros_distro="lyrical"

  docker run -dt --name "$CONTAINER_NAME" \
    "${PLATFORM_ARGS[@]+"${PLATFORM_ARGS[@]}"}" \
    --privileged \
    --hostname jetson-xbuild \
    -e PRODUCT="$PRODUCT" \
    -e ROS_DISTRO="$ros_distro" \
    -e ARM64_SYSROOT=/opt/arm64_sysroot \
    -e ROS2_WS=/home/ubuntu/ros2_ws \
    -e TOOLCHAINS_WS=/home/ubuntu/toolchains \
    -v "$ROS2_WS:/home/ubuntu/ros2_ws" \
    "${EXTRA_MOUNTS[@]+"${EXTRA_MOUNTS[@]}"}" \
    "${DOOD_ARGS[@]+"${DOOD_ARGS[@]}"}" \
    "$IMAGE" >/dev/null
  echo "Container created and started."
else
  echo "Container creation skipped."
  exit 0
fi

if confirm_no_default "Open a shell inside the container now?"; then
  exec docker exec -it "$CONTAINER_NAME" bash
fi

echo
echo "Setup complete."
echo "  docker exec -it \"$CONTAINER_NAME\" bash"
echo "  board list / board select <name> / board deploy --all"
