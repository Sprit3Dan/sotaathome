#!/usr/bin/env bash
set -Eeuo pipefail

# join.sh
# Ubuntu/Debian x86_64 host bootstrap for SoTA@Home GPU worker nodes.
#
# Current behavior:
# - safe by default for agents: non-interactive, no privileged installs unless explicitly allowed
# - verifies NVIDIA driver presence
# - installs Docker Engine if missing
# - installs NVIDIA Container Toolkit if missing
# - configures Docker to use NVIDIA runtime
# - installs Tailscale if missing
# - can bring Tailscale up interactively or with TAILSCALE_AUTHKEY
# - joins a k3s cluster as a GPU agent node when K3S_URL + K3S_TOKEN are provided
#
# Usage:
#   ./join.sh
#   sudo ./join.sh --interactive
#   sudo ./join.sh --dangerously-skip-permissions
#   K3S_URL=https://<server>:6443 K3S_TOKEN=<token> sudo ./join.sh --dangerously-skip-permissions
#
# Notes:
# - default mode is non-interactive and safe for agents
# - if a human step is needed, the script exits with guidance
# - --interactive allows guided privileged installs
# - --dangerously-skip-permissions allows one-time unattended install/config
# - k3s join is optional: skipped when K3S_URL/K3S_TOKEN are not set

SCRIPT_NAME="$(basename "$0")"

INTERACTIVE=0
DANGEROUS_SKIP_PERMISSIONS=0
TAILSCALE_AUTHKEY="${TAILSCALE_AUTHKEY:-}"
TAILSCALE_EXTRA_ARGS="${TAILSCALE_EXTRA_ARGS:-}"
K3S_URL="${K3S_URL:-}"
K3S_TOKEN="${K3S_TOKEN:-}"
APT_UPDATED=0
SUDO=""

STATE_FILE="/var/lib/join-sh/state"

record_installed() {
  local key="$1"
  $SUDO mkdir -p "$(dirname "$STATE_FILE")"
  if ! grep -qsF "${key}=1" "$STATE_FILE" 2>/dev/null; then
    echo "${key}=1" | $SUDO tee -a "$STATE_FILE" >/dev/null
  fi
}

log() {
  printf '[%s] %s\n' "$SCRIPT_NAME" "$*"
}

warn() {
  printf '[%s] WARNING: %s\n' "$SCRIPT_NAME" "$*" >&2
}

err() {
  printf '[%s] ERROR: %s\n' "$SCRIPT_NAME" "$*" >&2
}

die() {
  err "$*"
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  ./join.sh [--interactive] [--dangerously-skip-permissions] [--help]

Flags:
  --interactive
      Human-friendly mode. Allows guided installation and login steps.

  --dangerously-skip-permissions
      Allows one-time unattended package install/configuration using sudo/root.
      Intended for trusted automation on a fresh host.

Environment variables:
  TAILSCALE_AUTHKEY
      Optional. If set, tailscale can authenticate non-interactively.

  TAILSCALE_EXTRA_ARGS
      Optional. Extra args passed to 'tailscale up'.

  K3S_URL
      Optional. Control-plane URL for k3s cluster join (e.g. https://<server>:6443).
      Must be set together with K3S_TOKEN to join a cluster.

  K3S_TOKEN
      Optional. Shared secret for k3s cluster join.
      Must be set together with K3S_URL to join a cluster.

Examples:
  ./join.sh
  sudo ./join.sh --interactive
  sudo ./join.sh --dangerously-skip-permissions
  TAILSCALE_AUTHKEY=tskey-... sudo ./join.sh --dangerously-skip-permissions
  sudo K3S_URL=https://10.0.0.1:6443 K3S_TOKEN=secret ./join.sh --dangerously-skip-permissions
  sudo -E ./join.sh --dangerously-skip-permissions  # if env vars already exported
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --interactive)
        INTERACTIVE=1
        shift
        ;;
      --dangerously-skip-permissions)
        DANGEROUS_SKIP_PERMISSIONS=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "Unknown argument: $1"
        ;;
    esac
  done
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

can_install() {
  [[ "$DANGEROUS_SKIP_PERMISSIONS" -eq 1 || "$INTERACTIVE" -eq 1 ]]
}

install_mode_hint() {
  local thing="$1"
  cat >&2 <<EOF
$thing is missing.

Next steps:
  Human:
    sudo ./join.sh --interactive

  Agent / unattended:
    sudo ./join.sh --dangerously-skip-permissions
EOF
}

require_install_mode() {
  local thing="$1"
  if ! can_install; then
    install_mode_hint "$thing"
    exit 10
  fi
}

require_supported_os() {
  [[ -f /etc/os-release ]] || die "/etc/os-release not found"
  # shellcheck disable=SC1091
  . /etc/os-release

  case "${ID:-}" in
    ubuntu|debian)
      ;;
    *)
      die "This script currently supports Ubuntu/Debian only. Detected: ${ID:-unknown}"
      ;;
  esac

  ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
  case "$ARCH" in
    amd64|x86_64)
      ;;
    *)
      die "This script currently targets x86_64/amd64. Detected: $ARCH"
      ;;
  esac
}

sudo_check() {
  if [[ "$(id -u)" -eq 0 ]]; then
    SUDO=""
    return
  fi

  if need_cmd sudo; then
    if sudo -n true 2>/dev/null; then
      SUDO="sudo"
      return
    fi

    if [[ "$INTERACTIVE" -eq 1 ]]; then
      log "Checking sudo access interactively"
      sudo -v || die "Interactive sudo authentication failed"
      SUDO="sudo"
      return
    fi

    die "Need elevated privileges. Re-run as root, or use: sudo ./join.sh --interactive"
  fi

  die "sudo is not installed and script is not running as root."
}

wait_for_apt_lock() {
  local deadline=$(( $(date +%s) + 120 ))
  while flock -n /var/lib/dpkg/lock-frontend true 2>/dev/null; do
    return 0
  done
  log "Waiting for dpkg lock (held by another process)..."
  while ! flock -n /var/lib/dpkg/lock-frontend true 2>/dev/null; do
    if [[ $(date +%s) -ge $deadline ]]; then
      die "Timed out waiting for dpkg lock after 120s"
    fi
    sleep 3
  done
}

apt_update_once() {
  if [[ "$APT_UPDATED" -eq 0 ]]; then
    log "Running apt-get update"
    wait_for_apt_lock
    $SUDO apt-get update
    APT_UPDATED=1
  fi
}

apt_install() {
  local pkgs=("$@")
  wait_for_apt_lock
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y "${pkgs[@]}"
}

ensure_base_packages() {
  apt_update_once
  apt_install ca-certificates curl gnupg lsb-release apt-transport-https software-properties-common
}

check_nvidia_driver() {
  if need_cmd nvidia-smi; then
    if nvidia-smi >/dev/null 2>&1; then
      log "NVIDIA driver appears installed and working"
      return 0
    fi
  fi
  return 1
}

install_nvidia_driver_guidance() {
  cat >&2 <<'EOF'
NVIDIA driver is missing or not working.

This script intentionally does not auto-install the kernel driver because that is
the riskiest part of the stack and often depends on distro/kernel/GPU specifics.

Recommended next step on Ubuntu:
  sudo ubuntu-drivers autoinstall
  reboot

Then verify:
  nvidia-smi

After reboot, re-run:
  ./join.sh
EOF
}

docker_installed() {
  need_cmd docker
}

install_docker() {
  log "Installing Docker Engine from Docker's official apt repository"
  ensure_base_packages

  # shellcheck disable=SC1091
  . /etc/os-release
  local distro_id="$ID"
  local codename="${VERSION_CODENAME:-}"
  [[ -n "$codename" ]] || die "Could not determine VERSION_CODENAME from /etc/os-release"

  $SUDO install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${distro_id}/gpg" | \
    $SUDO gpg --batch --yes --dearmor -o /etc/apt/keyrings/docker.gpg
  $SUDO chmod a+r /etc/apt/keyrings/docker.gpg

  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${distro_id} ${codename} stable" | \
    $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null

  APT_UPDATED=0
  apt_update_once
  apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

  $SUDO systemctl enable --now docker
  record_installed DOCKER
  log "Docker installed"
}

ensure_docker() {
  if docker_installed; then
    log "Docker already installed"
  else
    require_install_mode "Docker"
    install_docker
  fi

  if ! $SUDO systemctl is-active --quiet docker; then
    log "Starting Docker"
    $SUDO systemctl enable --now docker
  fi
}

nvidia_toolkit_installed() {
  dpkg -s nvidia-container-toolkit 2>/dev/null | grep -q "^Status: install ok installed"
}

install_nvidia_container_toolkit() {
  log "Installing NVIDIA Container Toolkit"
  ensure_base_packages

  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
    $SUDO gpg --batch --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    $SUDO tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null

  APT_UPDATED=0
  apt_update_once
  apt_install nvidia-container-toolkit

  $SUDO nvidia-ctk runtime configure --runtime=docker
  $SUDO systemctl restart docker
  record_installed NVIDIA_CONTAINER_TOOLKIT
  log "NVIDIA Container Toolkit installed and Docker configured"
}

ensure_nvidia_container_toolkit() {
  if nvidia_toolkit_installed; then
    log "NVIDIA Container Toolkit already installed"
  else
    require_install_mode "NVIDIA Container Toolkit"
    install_nvidia_container_toolkit
  fi
}

verify_gpu_docker() {
  if ! check_nvidia_driver; then
    err "NVIDIA driver is not working"
    return 1
  fi

  if ! docker info >/dev/null 2>&1; then
    err "Docker is not responding"
    return 1
  fi

  log "Validating GPU visibility inside Docker"
  if docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi >/dev/null 2>&1; then
    log "GPU works inside Docker"
    return 0
  fi

  err "GPU is not available inside Docker"
  return 1
}

tailscale_installed() {
  need_cmd tailscale && need_cmd tailscaled
}

install_tailscale() {
  log "Installing Tailscale"
  curl -fsSL https://tailscale.com/install.sh | $SUDO sh
  $SUDO systemctl enable --now tailscaled
  record_installed TAILSCALE
}

ensure_tailscale() {
  if tailscale_installed; then
    log "Tailscale already installed"
  else
    require_install_mode "Tailscale"
    install_tailscale
  fi

  if ! $SUDO systemctl is-active --quiet tailscaled; then
    log "Starting tailscaled"
    $SUDO systemctl enable --now tailscaled
  fi
}

tailscale_is_up() {
  tailscale status >/dev/null 2>&1
}

ensure_tailscale_up() {
  if tailscale_is_up; then
    log "Tailscale is already connected"
    return 0
  fi

  if [[ -n "$TAILSCALE_AUTHKEY" ]]; then
    log "Bringing Tailscale up with auth key"
    $SUDO tailscale up --authkey="$TAILSCALE_AUTHKEY" $TAILSCALE_EXTRA_ARGS
    return 0
  fi

  if [[ "$INTERACTIVE" -eq 1 ]]; then
    log "Bringing Tailscale up interactively"
    $SUDO tailscale up $TAILSCALE_EXTRA_ARGS
    return 0
  fi

  # If a human is at a real terminal, run tailscale up automatically.
  # tailscale up prints a browser URL for the user to visit; this is not a
  # destructive system change, just a login step.
  if [[ -t 0 && -t 1 ]]; then
    log "Tailscale not connected. Starting authentication flow..."
    log "Open the URL below in a browser to authenticate (your invite grants access)."
    $SUDO tailscale up $TAILSCALE_EXTRA_ARGS
    return 0
  fi

  cat >&2 <<'EOF'
Tailscale is installed but not connected.

Next steps:
  Human (if you have a Tailscale invite):
    sudo ./join.sh
    (opens a browser URL — visit it to authenticate)

  Human (explicit interactive mode):
    sudo ./join.sh --interactive

  Agent / unattended:
    TAILSCALE_AUTHKEY=tskey-... sudo ./join.sh --dangerously-skip-permissions
EOF
  return 1
}

k3s_agent_installed() {
  [[ -f /usr/local/bin/k3s ]] && systemctl list-unit-files k3s-agent.service >/dev/null 2>&1
}

k3s_agent_running() {
  $SUDO systemctl is-active --quiet k3s-agent 2>/dev/null
}

install_k3s_agent() {
  log "Installing k3s agent and joining cluster"
  [[ -n "$K3S_URL" ]]   || die "K3S_URL is required to join a k3s cluster"
  [[ -n "$K3S_TOKEN" ]] || die "K3S_TOKEN is required to join a k3s cluster"

  # No manual containerd/nvidia config needed: k3s auto-detects the nvidia
  # container runtime at /usr/bin/nvidia-container-runtime and adds it to its
  # generated containerd config automatically. Writing config.toml.tmpl would
  # duplicate the nvidia table and crash containerd ("table already exists").

  # Write config.yaml before installing. This is more reliable than INSTALL_K3S_EXEC
  # (which is used verbatim as ExecStart and does NOT auto-inject --server from K3S_URL).
  $SUDO mkdir -p /etc/rancher/k3s
  cat <<EOF | $SUDO tee /etc/rancher/k3s/config.yaml >/dev/null
server: ${K3S_URL}
token: ${K3S_TOKEN}
node-label:
  - "sxfs.io/gpu=true"
EOF

  # K3S_URL tells the installer to install in agent role (not server).
  curl -sfL https://get.k3s.io | K3S_URL="$K3S_URL" K3S_TOKEN="$K3S_TOKEN" sh -

  record_installed K3S_AGENT
  log "k3s agent installed and joined cluster"
}

ensure_k3s_agent() {
  # k3s join is optional — skip when neither env var is set.
  if [[ -z "$K3S_URL" && -z "$K3S_TOKEN" ]]; then
    log "K3S_URL/K3S_TOKEN not set — skipping cluster join"
    log "  NOTE: 'export VAR=...' then 'sudo ./join.sh' won't work — sudo strips env vars."
    log "  Pass them inline: sudo K3S_URL=... K3S_TOKEN=... ./join.sh --dangerously-skip-permissions"
    log "  Or preserve env:  sudo -E ./join.sh --dangerously-skip-permissions"
    return 0
  fi

  # Both must be provided together.
  if [[ -z "$K3S_URL" || -z "$K3S_TOKEN" ]]; then
    die "Both K3S_URL and K3S_TOKEN must be set to join a k3s cluster (got only one)"
  fi

  if k3s_agent_running; then
    log "k3s agent already running"
    return 0
  fi

  require_install_mode "k3s agent"
  install_k3s_agent
}

summary() {
  echo
  echo "=== Summary ==="

  if check_nvidia_driver; then
    echo "NVIDIA driver: OK"
  else
    echo "NVIDIA driver: MISSING/BROKEN"
  fi

  if docker_installed; then
    echo "Docker: OK"
  else
    echo "Docker: MISSING"
  fi

  if nvidia_toolkit_installed; then
    echo "NVIDIA Container Toolkit: OK"
  else
    echo "NVIDIA Container Toolkit: MISSING"
  fi

  if tailscale_installed; then
    echo "Tailscale package: OK"
  else
    echo "Tailscale package: MISSING"
  fi

  if tailscale_is_up; then
    echo "Tailscale connection: OK"
    tailscale ip -4 2>/dev/null | sed 's/^/Tailscale IPv4: /' || true
  else
    echo "Tailscale connection: NOT CONNECTED"
  fi

  if [[ -z "$K3S_URL" && -z "$K3S_TOKEN" ]]; then
    echo "k3s agent: SKIPPED (K3S_URL/K3S_TOKEN not set)"
  elif k3s_agent_running; then
    echo "k3s agent: OK (running)"
  elif k3s_agent_installed; then
    echo "k3s agent: INSTALLED but not running"
  else
    echo "k3s agent: NOT JOINED"
  fi

  echo
}

main() {
  parse_args "$@"
  require_supported_os
  sudo_check

  log "Checking NVIDIA driver"
  if ! check_nvidia_driver; then
    install_nvidia_driver_guidance
    exit 2
  fi

  ensure_docker
  ensure_nvidia_container_toolkit

  if ! verify_gpu_docker; then
    die "Docker GPU validation failed. Check NVIDIA driver, toolkit, and Docker runtime configuration."
  fi

  ensure_tailscale
  if ! ensure_tailscale_up; then
    summary
    exit 3
  fi

  ensure_k3s_agent
  summary
  log "Bootstrap complete"
}

main "$@"
