#!/usr/bin/env bash
# Instala o Aegis no Moto E7 Plus e reinicia seu serviço.
set -euo pipefail
umask 077
base=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ $# -gt 1 || ${1:-ZF52366RHQ} != ZF52366RHQ ]]; then
    printf 'Uso: %s [ZF52366RHQ]\n' "$0" >&2
    exit 2
fi
adb=(adb -s ZF52366RHQ)
work=$(mktemp -d)
stage=''
cleanup() {
    if [[ -n "$stage" ]]; then "${adb[@]}" shell "rm -rf -- '$stage'" >/dev/null 2>&1 || true; fi
    rm -rf -- "$work"
}
trap cleanup EXIT

if [[ $("${adb[@]}" get-state 2>/dev/null) != device ]]; then
    printf '%s\n' 'Moto E7 Plus ZF52366RHQ indisponível via ADB.' >&2
    exit 1
fi
if [[ $("${adb[@]}" shell 'su -c "id -u"' | tr -d '\r') != 0 ]]; then
    printf '%s\n' 'Acesso root via su indisponível.' >&2
    exit 1
fi

cat > "$work/aegis_sentinel.sh" <<'SERVICE'
#!/system/bin/sh
umask 077
base=/data/adb/aegis_sentinel
export AEGIS_SQLITE=/system/bin/sqlite3
launcher=/data/adb/service.d/aegis_sentinel.sh
pidfile="$base/data/service.pid"
log="$base/data/service.log"

running() {
    read -r pid < "$pidfile" 2>/dev/null || return 1
    case "$pid" in ""|*[!0-9]*|0|1) return 1 ;; esac
    kill -0 "$pid" 2>/dev/null
}

case "${1:-}" in
    --run)
        exec 9>"$base/data/service.lock" || exit 1
        :
        staging=$(mktemp "$pidfile.XXXXXX") || exit 1
        if ! printf '%s\n' "$$" > "$staging" || ! mv -f -- "$staging" "$pidfile"; then
            rm -f -- "$staging"
            exit 1
        fi
        cleanup() {
            trap '' TERM INT HUP
            kill "$daemon" "$arp" "$pause" 2>/dev/null || :
            wait "$daemon" "$arp" "$pause" 2>/dev/null || :
            rm -f -- "$pidfile"
        }
        trap cleanup EXIT
        trap 'exit 0' TERM INT HUP
        while :; do
            /system/bin/sh "$base/aegis_daemon.sh" 9>&- &
            daemon=$!
            /system/bin/sh "$base/modules/arp_sentinel.sh" --loop 9>&- &
            arp=$!
            while kill -0 "$daemon" 2>/dev/null && kill -0 "$arp" 2>/dev/null; do
                sleep 3 9>&- & pause=$!
                wait "$pause"
            done
            kill "$daemon" "$arp" 2>/dev/null || :
            wait "$daemon" "$arp" 2>/dev/null || :
            sleep 5 9>&- & pause=$!
            wait "$pause"
        done ;;
    --stop)
        if running 2>/dev/null; then
            kill "$pid" || exit 1
            elapsed=0
            while running 2>/dev/null; do
                [ "$elapsed" -lt 20 ] || exit 1
                sleep 1
                elapsed=$((elapsed + 1))
            done
        fi ;;
    --status)
        running 2>/dev/null || exit 1
        db=$(/system/bin/sqlite3 -batch -bail :memory: "SELECT json_extract(CAST(readfile('$base/config.json') AS TEXT),'$.metrics_db');") || exit 1
        test -n "$db" && test -f "$db" || exit 1
        # O lock do daemon distingue a coleta em execução de um banco antigo.
        # Valida que o banco SQLite responde a queries
        tables_ok=$(/system/bin/sqlite3 -batch -bail -readonly -cmd '.timeout 5000' "$db" "SELECT count(*) FROM sqlite_master;" 2>/dev/null) || exit 1
        [ -n "$tables_ok" ] || exit 1
        ready=$(/system/bin/sqlite3 -batch -bail -readonly -cmd '.timeout 5000' "$db" \
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('power_status','network_cycles','arp_devices');") || exit 1
        [ "$ready" = 3 ] ;;
    --start|'')
        if [ -z "${1:-}" ]; then
            elapsed=0
            while [ "$(getprop sys.boot_completed)" != 1 ] && [ "$elapsed" -lt 90 ]; do
                sleep 2
                elapsed=$((elapsed + 1))
            done
        fi
        running 2>/dev/null && exit 0
        setsid /system/bin/sh "$launcher" --run </dev/null >>"$log" 2>&1 & ;;
    *) exit 2 ;;
esac
SERVICE

stage=$("${adb[@]}" shell 'umask 077; mktemp -d /data/local/tmp/aegis-stage.XXXXXX' | tr -d '\r')
if [[ ! "$stage" =~ ^/data/local/tmp/aegis-stage\.[a-zA-Z0-9]+$ ]]; then
    stage=''
    printf '%s\n' 'Falha ao preparar staging no Moto.' >&2
    exit 1
fi
"${adb[@]}" push "$base/device_root/aegis_daemon.sh" "$base/device_root/aegis_daemon.py" \
    "$base/device_root/config.json" "$base/device_root/modules" "$work/aegis_sentinel.sh" "$stage/" >/dev/null

"${adb[@]}" shell 'su -c /system/bin/sh' <<REMOTE
set -eu
umask 077
base=/data/adb/aegis_sentinel
export AEGIS_SQLITE=/system/bin/sqlite3
launcher=/data/adb/service.d/aegis_sentinel.sh
stage='$stage'
mkdir -p "\$base/modules" "\$base/data" /data/adb/service.d
chmod 0700 "\$base" "\$base/modules" "\$base/data"
exec 8>"\$base/data/install.lock"
:
/system/bin/sqlite3 -batch -bail :memory: "SELECT json_valid('{}');" | grep -qx 1
if [ -f "\$launcher" ]; then /system/bin/sh "\$launcher" --stop; fi

install_file() {
    temp=\$(mktemp "\$2.XXXXXX")
    if ! cp "\$1" "\$temp" || ! chmod "\$3" "\$temp" || ! mv -f -- "\$temp" "\$2"; then
        rm -f -- "\$temp"
        return 1
    fi
}
for name in aegis_daemon.sh aegis_daemon.py; do
    install_file "\$stage/\$name" "\$base/\$name" 0700
done
for name in smart_profile.sh power_watchdog.sh network_auditor.sh wol_bridge.sh arp_sentinel.sh; do
    install_file "\$stage/modules/\$name" "\$base/modules/\$name" 0700
done
if [ ! -e "\$base/config.json" ]; then
    install_file "\$stage/config.json" "\$base/config.json" 0600
else
    chmod 0600 "\$base/config.json"
fi
install_file "\$stage/aegis_sentinel.sh" "\$launcher" 0755
/system/bin/sh "\$launcher" --start 8>&-
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if /system/bin/sh "\$launcher" --status; then
        # Detecta reinícios logo após a abertura inicial do SQLite.
        sleep 3
        /system/bin/sh "\$launcher" --status && exit 0
    fi
done
printf '%s\n' 'Aegis: smoke test falhou. Consulte /data/adb/aegis_sentinel/data/service.log.' >&2
exit 1
REMOTE
printf '%s\n' 'Aegis instalado e ativo no Moto E7 Plus ZF52366RHQ.'
