#!/usr/bin/env bash
set -euo pipefail

action=''
if [[ $# -eq 1 ]]; then
    case "$1" in
        --sentry) action='sync_profile_with_power 1' ;;
        --portable) action='sync_profile_with_power 0' ;;
        --status) action=show_profile_status ;;
    esac
fi
if [[ -z ${action:-} ]]; then
    printf 'Uso: %s --sentry|--portable|--status\n' "$0" >&2
    exit 2
fi

exec adb -s ZF52366RHQ shell "su -c 'base=/data/adb/aegis_sentinel; . /data/adb/aegis_sentinel/modules/smart_profile.sh || exit 1; $action'"
