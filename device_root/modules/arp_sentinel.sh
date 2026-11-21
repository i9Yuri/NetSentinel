#!/system/bin/sh
# Inventário passivo. Vizinhos reaparecem pelo mesmo MAC, sem novo registro.
umask 077
export LC_ALL=C
base=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd) || exit 1

sqlite_run() {
    if [ -n "${AEGIS_PYTHON:-}" ]; then
        "$AEGIS_PYTHON" "$base/aegis_daemon.py" --sqlite "$1"
    elif [ -z "${AEGIS_SQLITE:-}" ] && [ ! -x /system/bin/sqlite3 ] &&
        command -v python3 >/dev/null 2>&1; then
        python3 "$base/aegis_daemon.py" --sqlite "$1"
    else
        "${AEGIS_SQLITE:-/system/bin/sqlite3}" -batch -bail -noheader -cmd '.timeout 5000' "$1"
    fi
}

db=${AEGIS_DB:-}
if [ -z "$db" ]; then
    config_path=$(printf '%s' "${AEGIS_CONFIG:-$base/config.json}" | sed "s/'/''/g")
    db=$(printf "SELECT json_extract(CAST(readfile('%s') AS TEXT),'$.metrics_db');\n" "$config_path" |
        sqlite_run :memory:) || exit 1
fi
[ -n "$db" ] && mkdir -p -- "$(dirname -- "$db")" || exit 1

scan() {
    {
        cat "${AEGIS_ARP_FILE:-/proc/net/arp}" 2>/dev/null
        ip -4 neigh show 2>/dev/null
    } | awk '
        function valid_ip(address, parts, i) {
            if (split(address, parts, ".") != 4) return 0
            for (i=1;i<=4;i++)
                if (parts[i] !~ /^[0-9]+$/ || length(parts[i])>3 || parts[i]>255) return 0
            return 1
        }
        function remember(address, mac, iface) {
            mac=tolower(mac)
            if (valid_ip(address) && mac ~ /^([0-9a-f][02468ace]:)([0-9a-f]{2}:){4}[0-9a-f]{2}$/ &&
                mac != "00:00:00:00:00:00" && iface ~ /^[A-Za-z0-9_.:@-]+$/) {
                addresses[mac]=address
                interfaces[mac]=iface
            }
        }
        BEGIN {
            print "BEGIN IMMEDIATE;"
            print "CREATE TABLE IF NOT EXISTS arp_devices (mac TEXT PRIMARY KEY, ip TEXT NOT NULL, interface TEXT NOT NULL, first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL);"
            q=sprintf("%c",39)
        }
        $2 ~ /^0x[0-9a-f]+$/ && $3 ~ /^0x[0-9a-f]*[2367abef]$/ {remember($1,$4,$6)}
        !/ (FAILED|INCOMPLETE)( |$)/ {
            for (i=4;i<NF;i++)
                if ($2=="dev" && $i=="lladdr") remember($1,$(i+1),$3)
        }
        END {
            for (mac in addresses) {
                printf "INSERT INTO arp_devices VALUES (%s%s%s,%s%s%s,%s%s%s,unixepoch(),unixepoch()) ON CONFLICT(mac) DO UPDATE SET ip=excluded.ip, interface=excluded.interface, last_seen=excluded.last_seen;\n",
                    q,mac,q,q,addresses[mac],q,q,interfaces[mac],q
            }
            print "COMMIT;"
        }' | sqlite_run "$db"
}

case "${1:---once}" in
    --once) scan ;;
    --loop)
        trap 'exit 0' TERM INT HUP
        while :; do
            scan || printf '%s\n' 'Aegis: falha ao atualizar o inventário ARP.' >&2
            sleep 60
        done ;;
    *) printf '%s\n' 'Uso: arp_sentinel.sh [--once|--loop]' >&2; exit 2 ;;
esac
