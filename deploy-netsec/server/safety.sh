#!/usr/bin/env bash
set -euo pipefail

readonly PROTECTED_PATHS=(/opt/aichat /etc/aichat /var/backups/aichat)
readonly PROTECTED_UNIT_PREFIX=aichat-
readonly PROTECTED_PORTS=(3000 4100 5432 4200)
readonly PROTECTED_DOCKER_PREFIX=compose
readonly PROTECTED_HOSTNAME=ayur.namahos.com
readonly ALLOWED_ROOT=/opt/netsecai
readonly ALLOWED_SECRETS=/etc/netsecai
readonly ALLOWED_BACKUPS=/var/backups/netsecai
readonly ALLOWED_UNIT_PREFIX=netsecai-

ns_die() { printf '\nSTOP: %s\n' "$*" >&2; exit 1; }

assert_safe_target() {
  local value="$1"
  [ -n "$value" ] || ns_die "empty mutation target"
  for path in "${PROTECTED_PATHS[@]}"; do
    [ "$value" = "$path" ] && ns_die "protected path refused: $value"
    case "$value" in "$path"/*) ns_die "protected path refused: $value" ;; esac
  done
  case "$value" in
    $PROTECTED_UNIT_PREFIX*) ns_die "protected unit refused: $value" ;;
    $PROTECTED_DOCKER_PREFIX*) ns_die "protected Docker resource refused: $value" ;;
    $PROTECTED_HOSTNAME) ns_die "protected hostname refused: $value" ;;
  esac
  for port in "${PROTECTED_PORTS[@]}"; do
    [ "$value" != "$port" ] || ns_die "protected port refused: $value"
  done
}

assert_netsec_path() {
  [ "$1" = "$ALLOWED_ROOT" ] && return 0
  case "$1" in $ALLOWED_ROOT/*) return 0 ;; esac
  ns_die "non-NetSec path refused: $1"
}

assert_netsec_unit() { case "$1" in $ALLOWED_UNIT_PREFIX*) ;; *) ns_die "non-NetSec unit refused: $1" ;; esac; }
assert_netsec_port() { case "$1" in 4300|4400|55432) ;; *) ns_die "non-NetSec port refused: $1" ;; esac; }

safety_preflight() {
  assert_netsec_path "$ALLOWED_ROOT"
  assert_safe_target "$ALLOWED_ROOT"
  assert_safe_target "$ALLOWED_SECRETS"
  assert_safe_target "$ALLOWED_BACKUPS"
  assert_netsec_unit "$ALLOWED_UNIT_PREFIX"
  for port in 4300 4400 55432; do assert_netsec_port "$port"; done
}
