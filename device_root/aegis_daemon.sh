#!/system/bin/sh
# AEGIS_CONFIG and AEGIS_DB allow isolated runs on a host.
umask 077
export LC_ALL=C
base=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) || exit 1

sqlite_cli() {
    if [ -n "${AEGIS_PYTHON:-}" ]; then
        "$AEGIS_PYTHON" "$base/aegis_daemon.py" --sqlite "$1"
    else
        "${AEGIS_SQLITE:-/system/bin/sqlite3}" -batch -bail -noheader -cmd '.timeout 5000' "$1"
    fi
}

sql() {
    printf '%s\n' "$1" | sqlite_cli "$db"
}

sql_text() {
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/''/g")"
}

uptime_seconds() (
    read -r seconds rest < /proc/uptime || exit 1
    printf '%s\n' "${seconds%%.*}"
)

load_config() {
    config_file=${AEGIS_CONFIG:-$base/config.json}
    # SQLite reads the file itself. The token never appears in a process argument.
    config_sql="CAST(readfile($(sql_text "$config_file")) AS TEXT)"
    config() {
        printf 'SELECT %s FROM (SELECT %s AS cfg);\n' "$1" "$config_sql" |
            sqlite_cli :memory: 2>/dev/null
    }
    config 'json_valid(cfg)' | grep -qx 1 || return 1
    db=${AEGIS_DB:-$(config "json_extract(cfg, '$.metrics_db')")}
    token=$(config "json_extract(cfg, '$.telegram_bot_token')")
    chat=$(config "json_extract(cfg, '$.telegram_chat_id')")
    location=$(config "json_extract(cfg, '$.location_label')")
    xeon_ip=$(config "json_extract(cfg, '$.xeon_ip')")
    xeon_mac=$(config "json_extract(cfg, '$.xeon_mac')")
    broadcast_ip=$(config "json_extract(cfg, '$.broadcast_ip')")
    gateway=$(config "coalesce(json_extract(cfg, '$.gateway_ip'), '192.168.0.1')")
    targets=$(config "(SELECT group_concat(value, char(10)) FROM json_each(cfg, '$.ping_targets'))")
    domains=$(config "(SELECT group_concat(value, char(10)) FROM json_each(cfg, '$.dns_test_domains'))")
    ptt=$(config "json_extract(cfg, '$.ptt_target')")
    [ -n "$db" ] && [ -n "$location" ] || return 1
    [ -z "$token" ] || {
        printf '%s\n' "$token" | grep -Eq '^[0-9]+:[A-Za-z0-9_-]+$' &&
        printf '%s\n' "$chat" | grep -Eq '^[1-9][0-9]{0,15}$'
    }
}

init_db() {
    mkdir -p -- "$(dirname -- "$db")" &&
    sql "PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS power_status (
        id INTEGER PRIMARY KEY CHECK (id = 1), sampled_at INTEGER NOT NULL,
        ac INTEGER, battery_pct INTEGER, temp_tenths INTEGER,
        battery_uv INTEGER, battery_ua INTEGER, usb_uv INTEGER
    );
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY, at INTEGER NOT NULL, kind TEXT NOT NULL,
        duration_s INTEGER, message TEXT, sent INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS events_kind ON events(kind, id);
    CREATE INDEX IF NOT EXISTS events_pending ON events(id) WHERE sent=0 AND message IS NOT NULL;
    CREATE TABLE IF NOT EXISTS network_cycles (
        id INTEGER PRIMARY KEY, at INTEGER NOT NULL, duration_s INTEGER NOT NULL,
        severe INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS network_metrics (
        cycle_id INTEGER NOT NULL, target TEXT NOT NULL, role TEXT NOT NULL,
        rtt_ms REAL, jitter_ms REAL, loss_pct REAL,
        PRIMARY KEY(cycle_id, role, target)
    );
    CREATE INDEX IF NOT EXISTS network_streak ON network_metrics(role, target, cycle_id);
    CREATE TABLE IF NOT EXISTS dns_metrics (
        cycle_id INTEGER NOT NULL, domain TEXT NOT NULL, resolver TEXT NOT NULL,
        elapsed_ms REAL, ok INTEGER NOT NULL, PRIMARY KEY(cycle_id, domain)
    );
    CREATE TABLE IF NOT EXISTS telegram_cursor (
        chat INTEGER PRIMARY KEY, next_id INTEGER NOT NULL
    );" >/dev/null
}

. "$base/modules/power_watchdog.sh"
. "$base/modules/network_auditor.sh"

telegram() (
    method=$1
    shift
    # Ignore curlrc and pass the URL via stdin, with no diagnostic containing it.
    printf 'url = "https://api.telegram.org/bot%s/%s"\n' "$token" "$method" |
        curl -q --config - --proto '=https' --silent --fail --connect-timeout 4 \
            --max-time 23 --max-filesize 131072 "$@" 2>/dev/null
)

telegram_send() (
    result=$(telegram sendMessage --data-urlencode "chat_id=$chat" --data-urlencode "text=$1") ||
        exit 1
    [ "$(telegram_json "$result" "json_extract(body, '$.ok')")" = 1 ]
)

telegram_json() (
    payload=$(printf '%s' "$1" | od -An -v -tx1 | tr -d ' \n')
    # Hex encoding keeps JSON data out of executable SQL.
    printf "WITH input(body) AS (SELECT CAST(X'%s' AS TEXT))\nSELECT %s;\n" "$payload" "$2" |
        sqlite_cli :memory: 2>/dev/null
)

handle_updates() (
    offset=$(sql "SELECT next_id FROM telegram_cursor WHERE chat=$chat;") || exit 1
    result=$(telegram getUpdates --data-urlencode "offset=${offset:-0}" --data 'limit=10' \
        --data 'timeout=12' --data 'allowed_updates=["message"]') || exit 1
    updates=$(telegram_json "$result" "
      printf('%lld|%s', json_extract(value,'$.update_id'),
        CASE WHEN json_extract(value,'$.message.chat.id')=$chat
          AND json_extract(value,'$.message.from.id')=$chat
          AND json_extract(value,'$.message.chat.type')='private'
          AND json_extract(value,'$.message.date') BETWEEN unixepoch()-120 AND unixepoch()+15
          THEN CASE json_extract(value,'$.message.text')
            WHEN '/status' THEN 'status'
            WHEN '/wol_desktop' THEN 'wol'
            WHEN '/net_report' THEN 'report' ELSE '' END
          ELSE '' END)
      FROM input, json_each(body,'$.result')
      WHERE json_extract(body,'$.ok')=1 AND json_type(value,'$.update_id')='integer'
      ORDER BY json_extract(value,'$.update_id')") || exit 1
    while IFS='|' read -r update command; do
        case "$update" in ''|*[!0-9]*) continue ;; esac
        [ "$update" -ge "${offset:-0}" ] || continue
        # Durable acknowledgement before sending a packet prevents replay after restart.
        offset=$((update + 1))
        sql "INSERT OR REPLACE INTO telegram_cursor VALUES ($chat,$offset);" || exit 1
        case "$command" in
            status) telegram_send "$(status_message)" || : ;;
            wol)
                if sh "$base/modules/wol_bridge.sh" "$xeon_mac" "$broadcast_ip" >/dev/null 2>&1; then
                    telegram_send 'Magic Packet enviado. Use /status para verificar a Workstation.' || :
                else
                    telegram_send 'Falha no envio do Magic Packet.' || :
                fi ;;
            report) telegram_send "$(network_message)" || : ;;
        esac
    done <<UPDATES
$updates
UPDATES
)

telegram_loop() {
    while :; do
        pending=$(sql 'SELECT id FROM events WHERE sent=0 AND message IS NOT NULL ORDER BY id LIMIT 10;')
        for event_id in $pending; do
            message=$(sql "SELECT message FROM events WHERE id=$event_id;") && [ -n "$message" ] || break
            telegram_send "$message" || break
            sql "UPDATE events SET sent=1 WHERE id=$event_id;" || break
        done
        handle_updates || sleep 15
    done
}

status_message() {
    sql "SELECT 'Aegis-Sentinel | ' || $(sql_text "$location") || char(10) ||
        'Energia: ' || CASE ac WHEN 1 THEN 'conectada' WHEN 0 THEN 'desconectada' ELSE 'sem leitura' END ||
        char(10) || 'Bateria: ' || coalesce(battery_pct || '%', 'sem leitura') ||
        CASE WHEN temp_tenths IS NULL THEN '' ELSE printf(' | %.1f °C', temp_tenths / 10.0) END
        FROM power_status;" | sed '/^$/d'
    if valid_ipv4 "$xeon_ip" && ping -c 1 -W 1 "$xeon_ip" >/dev/null 2>&1; then
        printf '%s\n' 'Workstation: responde na rede.'
    else
        printf '%s\n' 'Workstation: sem resposta ICMP.'
    fi
}

load_config && init_db || { printf '%s\n' 'Aegis: configuração ou SQLite indisponível.' >&2; exit 1; }
lock_file="$db.lock"
flock_cmd() {
    if [ -x /data/adb/magisk/busybox ]; then
        /data/adb/magisk/busybox flock "$@" 2>/dev/null || true
        return 0
    fi
    return 0
}
case "${1:-}" in
    --power) exec 9>"$lock_file"; flock_cmd -n 9 || exit 1; power_loop; exit ;;
    --network-once) exec 9>"$lock_file"; flock_cmd -n 9 || exit 1; network_cycle; exit ;;
    '') ;;
    *) printf '%s\n' 'Uso: aegis_daemon.sh [--power|--network-once]' >&2; exit 2 ;;
esac
exec 9>"$lock_file"
flock_cmd -n 9 || { printf '%s\n' 'Aegis: daemon já em execução.' >&2; exit 1; }

workers=''
wake_name=aegis_sentinel_watchdog
cleanup() {
    trap '' INT TERM HUP
    for worker in $workers; do kill "$worker" 2>/dev/null || :; done
    [ ! -w /sys/power/wake_unlock ] || printf '%s\n' "$wake_name" > /sys/power/wake_unlock
}
trap cleanup EXIT
trap 'exit 0' INT TERM HUP
# Mantém a amostragem de 1 s quando a tela Android está apagada.
[ ! -w /sys/power/wake_lock ] || printf '%s\n' "$wake_name" > /sys/power/wake_lock
power_loop 9>&- & workers="$!"
network_loop 9>&- & workers="$workers $!"
if [ -n "$token" ]; then telegram_loop 9>&- & workers="$workers $!"; fi
while :; do
    for worker in $workers; do kill -0 "$worker" 2>/dev/null || exit 1; done
    sleep 5
done
