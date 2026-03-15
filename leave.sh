#!/usr/bin/env bash
set -Eeuo pipefail

# leave.sh
# Reverses what join.sh installed, based on the state file written during join.
#
# Only removes components that join.sh actually installed (not pre-existing ones).
#
# Usage:
#   sudo ./leave.sh
#   sudo ./leave.sh --dangerously-skip-permissions
#   sudo ./leave.sh --dry-run

SCRIPT_NAME="$(basename "$0")"
STATE_FILE="/var/lib/join-sh/state"

INTERACTIVE=0
DANGEROUS_SKIP_PERMISSIONS=0
DRY_RUN=0
SUDO=""

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
  sudo ./leave.sh [--dangerously-skip-permissions] [--dry-run] [--help]

Reads /var/lib/join-sh/state and removes only what join.sh installed.

Flags:
  --dangerously-skip-permissions
      Allows unattended removal using sudo/root.

  --dry-run
      Print what would be done without making changes.

  --interactive
      Human-friendly mode (same effect as --dangerously-skip-permissions here).
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
      --dry-run)
        DRY_RUN=1
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

    die "Need elevated privileges. Re-run as root, or use: sudo ./leave.sh"
  fi

  die "sudo is not installed and script is not running as root."
}

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

load_state() {
  if [[ ! -f "$STATE_FILE" ]]; then
    die "State file not found: $STATE_FILE — nothing to clean up (join.sh may not have run, or state was already cleared)"
  fi

  INSTALLED_DOCKER=0
  INSTALLED_NVIDIA_CONTAINER_TOOLKIT=0
  INSTALLED_TAILSCALE=0

  # shellcheck disable=SC1090
  while IFS='=' read -r key val; do
    case "$key" in
      DOCKER)                    INSTALLED_DOCKER="$val" ;;
      NVIDIA_CONTAINER_TOOLKIT)  INSTALLED_NVIDIA_CONTAINER_TOOLKIT="$val" ;;
      TAILSCALE)                 INSTALLED_TAILSCALE="$val" ;;
    esac
  done < "$STATE_FILE"
}

remove_tailscale() {
  log "Removing Tailscale"

  if need_cmd tailscale; then
    run $SUDO tailscale logout 2>/dev/null || true
    run $SUDO tailscale down 2>/dev/null || true
  fi

  run $SUDO systemctl stop tailscaled 2>/dev/null || true
  run $SUDO systemctl disable tailscaled 2>/dev/null || true

  if need_cmd apt-get; then
    run $SUDO apt-get remove -y tailscale 2>/dev/null || true
    run $SUDO apt-get autoremove -y 2>/dev/null || true
  fi

  # Remove Tailscale apt repo if present
  run $SUDO rm -f /etc/apt/sources.list.d/tailscale.list
  run $SUDO rm -f /usr/share/keyrings/tailscale-archive-keyring.gpg

  log "Tailscale removed"
}

remove_nvidia_container_toolkit() {
  log "Removing NVIDIA Container Toolkit"

  run $SUDO systemctl stop docker 2>/dev/null || true

  run $SUDO apt-get remove -y nvidia-container-toolkit 2>/dev/null || true
  run $SUDO apt-get autoremove -y 2>/dev/null || true

  # Remove NVIDIA Container Toolkit apt repo and keyring
  run $SUDO rm -f /etc/apt/sources.list.d/nvidia-container-toolkit.list
  run $SUDO rm -f /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

  # Remove NVIDIA runtime config from Docker daemon
  if [[ -f /etc/docker/daemon.json ]]; then
    log "Removing NVIDIA runtime from /etc/docker/daemon.json"
    if [[ "$DRY_RUN" -eq 0 ]]; then
      # Remove the file if it only contained the nvidia config; otherwise leave it
      if $SUDO python3 -c "
import json, sys
with open('/etc/docker/daemon.json') as f:
    d = json.load(f)
d.pop('runtimes', None)
d.pop('default-runtime', None)
if d:
    with open('/etc/docker/daemon.json', 'w') as f:
        json.dump(d, f, indent=2)
    sys.exit(0)
else:
    sys.exit(1)
" 2>/dev/null; then
        log "Updated /etc/docker/daemon.json"
      else
        run $SUDO rm -f /etc/docker/daemon.json
      fi
    else
      printf '[dry-run] patch or remove /etc/docker/daemon.json\n'
    fi
  fi

  run $SUDO systemctl start docker 2>/dev/null || true

  log "NVIDIA Container Toolkit removed"
}

remove_docker() {
  log "Removing Docker Engine"

  run $SUDO systemctl stop docker 2>/dev/null || true
  run $SUDO systemctl disable docker 2>/dev/null || true
  run $SUDO systemctl stop containerd 2>/dev/null || true
  run $SUDO systemctl disable containerd 2>/dev/null || true

  run $SUDO apt-get remove -y \
    docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin 2>/dev/null || true
  run $SUDO apt-get autoremove -y 2>/dev/null || true

  # Remove Docker apt repo and keyring
  run $SUDO rm -f /etc/apt/sources.list.d/docker.list
  run $SUDO rm -f /etc/apt/keyrings/docker.gpg

  log "Docker removed"
}

clear_state() {
  log "Removing state file: $STATE_FILE"
  run $SUDO rm -f "$STATE_FILE"
  run $SUDO rmdir "$(dirname "$STATE_FILE")" 2>/dev/null || true
}

main() {
  parse_args "$@"
  sudo_check
  load_state

  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "Dry-run mode — no changes will be made"
  fi

  local did_something=0

  if [[ "$INSTALLED_TAILSCALE" -eq 1 ]]; then
    remove_tailscale
    did_something=1
  else
    log "Tailscale was not installed by join.sh — skipping"
  fi

  if [[ "$INSTALLED_NVIDIA_CONTAINER_TOOLKIT" -eq 1 ]]; then
    remove_nvidia_container_toolkit
    did_something=1
  else
    log "NVIDIA Container Toolkit was not installed by join.sh — skipping"
  fi

  if [[ "$INSTALLED_DOCKER" -eq 1 ]]; then
    remove_docker
    did_something=1
  else
    log "Docker was not installed by join.sh — skipping"
  fi

  if [[ "$did_something" -eq 1 ]]; then
    clear_state
    log "Cleanup complete"
  else
    log "Nothing to clean up (no components were recorded as installed by join.sh)"
  fi
}

main "$@"
