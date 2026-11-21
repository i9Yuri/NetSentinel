#!/system/bin/sh
# O daemon chama este módulo com MAC e destino. --check retorna o resultado ICMP na CLI.
umask 077
export LC_ALL=C
base=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd) || exit 1
. "$base/modules/network_auditor.sh"

wol_sqlite() {
    if [ -n "${AEGIS_PYTHON:-}" ]; then
        "$AEGIS_PYTHON" "$base/aegis_daemon.py" --sqlite "$1"
    elif [ -z "${AEGIS_SQLITE:-}" ] && [ ! -x /system/bin/sqlite3 ] &&
        command -v python3 >/dev/null 2>&1; then
        python3 "$base/aegis_daemon.py" --sqlite "$1"
    else
        "${AEGIS_SQLITE:-/system/bin/sqlite3}" -batch -bail -noheader -cmd '.timeout 5000' "$1"
    fi
}

check_desktop() {
    sleep 15
    if ping -n -c 1 -W 2 "$desktop" >/dev/null 2>&1; then
        message="Workstation respondeu ao ping após o Wake-on-LAN ($desktop)."
    else
        message="Workstation sem resposta ICMP após o Wake-on-LAN ($desktop)."
    fi
}

check=0
if [ "${1:-}" = --check ]; then check=1; shift; fi

config_path=$(printf '%s' "${AEGIS_CONFIG:-$base/config.json}" | sed "s/'/''/g")
config_values=$(printf "SELECT json_extract(cfg,'$.xeon_mac'), json_extract(cfg,'$.broadcast_ip'), json_extract(cfg,'$.xeon_ip'), json_extract(cfg,'$.metrics_db') FROM (SELECT CAST(readfile('%s') AS TEXT) cfg);\n" "$config_path" |
    wol_sqlite :memory: 2>/dev/null) || config_values=''
IFS='|' read -r configured_mac configured_destination configured_desktop configured_db <<CONFIG
$config_values
CONFIG
mac=${1:-${configured_mac:-00:c0:c6:25:81:52}}
destination=${2:-${configured_destination:-192.168.0.255}}
desktop=${3:-${configured_desktop:-192.168.0.14}}
db=${AEGIS_DB:-$configured_db}

case "$destination|$desktop" in *[!0-9.\|]*) exit 2 ;; esac
case "$mac" in *[!0-9a-fA-F:]*) exit 2 ;; esac
if [ "$#" -gt 3 ] || ! valid_ipv4 "$destination" || ! valid_ipv4 "$desktop" ||
    ! printf '%s\n' "$mac" | grep -Eq '^([[:xdigit:]]{2}:){5}[[:xdigit:]]{2}$'; then
    printf '%s\n' 'Uso: wol_bridge.sh [--check] [MAC [destino IPv4 [IP da Workstation]]]' >&2
    exit 2
fi

send_magic() {
    if [ -n "${AEGIS_PYTHON:-}" ] || command -v python3 >/dev/null 2>&1; then
        "${AEGIS_PYTHON:-python3}" "$base/aegis_daemon.py" --send-wol "$mac" "$destination" && return 0
    fi

    # Nul bytes ficam no fluxo do printf, nunca em variáveis do shell.
    octets=$(printf '%s\n' "$mac" | tr ':' ' ')
    suffix=''
    for octet in $octets; do
        suffix="$suffix$(printf '\\%03o' "0x$octet")"
    done
    packet='\377\377\377\377\377\377'
    count=0
    while [ "$count" -lt 16 ]; do
        packet="$packet$suffix"
        count=$((count + 1))
    done

    if command -v nc >/dev/null 2>&1; then
        # OpenBSD nc precisa de -b. Toybox pode rejeitar UDP broadcast.
        printf "$packet" | nc -b -u -w 2 "$destination" 9 >/dev/null 2>&1 && return 0
        printf "$packet" | nc -u -w 2 -W 1 "$destination" 9 >/dev/null 2>&1 && return 0
    fi

    # Quando o nc não permite broadcast, tenta o quadro Ethernet nativo do Magisk.
    iface=$(ip -4 route get "$desktop" 2>/dev/null |
        awk 'NR == 1 {for (i=1; i<NF; i++) if ($i == "dev") {print $(i+1); exit}}')
    if [ "$destination" != "$desktop" ] && [ -n "$iface" ]; then
        if command -v ether-wake >/dev/null 2>&1; then
            ether-wake -b -i "$iface" "$mac" && return 0
        elif [ -x /data/adb/magisk/busybox ]; then
            /data/adb/magisk/busybox ether-wake -b -i "$iface" "$mac" && return 0
        fi
        # UDP unicast depende de o destino ainda estar alcançável na camada 2.
        printf "$packet" | nc -u -w 2 -W 1 "$desktop" 9 >/dev/null 2>&1 && return 0
    fi
    return 1
}

send_magic || { printf '%s\n' 'Falha no envio do Magic Packet.' >&2; exit 1; }
if [ "$check" -eq 1 ]; then
    check_desktop
    printf 'Magic Packet enviado. %s\n' "$message"
elif [ -n "$db" ] && [ -f "$db" ]; then
    # O chamador envia a confirmação imediata. O resultado entra na fila de Telegram.
    (
        check_desktop
        printf "INSERT INTO events(at,kind,message) VALUES (unixepoch(),'WOL_RESULT','%s');\n" "$message" |
            wol_sqlite "$db"
    ) </dev/null >/dev/null 2>&1 &
fi
