#!/system/bin/sh
# Carregado por aegis_daemon.sh. Jitter é a diferença absoluta média entre RTTs sucessivos.
valid_ipv4() {
    printf '%s\n' "$1" | awk -F. '
        NF != 4 {exit 1}
        {for (i=1;i<=4;i++) if ($i !~ /^[0-9]+$/ || length($i)>3 || $i>255) exit 1}'
}

ping_metrics() {
    ping -n -c "$2" -W 2 -s 64 "$1" 2>/dev/null | awk '
        match($0, /time[=<][0-9.]+/) {
            value=substr($0,RSTART+5,RLENGTH-5)+0
            sum+=value
            if (count++) {delta=value-prev; jitter+=(delta<0 ? -delta : delta)}
            prev=value
        }
        /packet loss/ {
            for (i=1;i<=NF;i++) if ($i ~ /^[0-9.]+%$/) loss=$i+0
        }
        END {
            mean=count ? sprintf("%.3f",sum/count) : "NULL"
            jump=count>1 ? sprintf("%.3f",jitter/(count-1)) : "NULL"
            printf "%s,%s,%s\n",mean,jump,(loss=="" ? "NULL" : loss)
        }'
}

dns_lookup() {
    if command -v nslookup >/dev/null 2>&1; then
        nslookup "$1" "$2"
    elif [ -x /data/adb/magisk/busybox ]; then
        /data/adb/magisk/busybox nslookup "$1" "$2"
    else
        return 127
    fi
}

monotonic_ms() (
    read -r up rest < /proc/uptime || exit 1
    awk -v stamp="$up" 'BEGIN {printf "%.0f\n",stamp*1000}'
)

network_cycle() (
    started=$(uptime_seconds) || exit 1
    at=$(date +%s)
    cycled='(SELECT max(id) FROM network_cycles)'
    rows=''
    measure() {
        valid_ipv4 "$2" || return 0
        metrics=$(ping_metrics "$2" "$3")
        rows="$rows
          INSERT OR IGNORE INTO network_metrics VALUES ($cycled,'$2','$1',$metrics);"
    }
    measure gateway "$gateway" 10
    resolver=''
    for target in $targets; do
        valid_ipv4 "$target" || continue
        [ -n "$resolver" ] || resolver=$target
        measure dns "$target" 10
    done
    measure ix "$ptt" 10
    measure desktop "$xeon_ip" 1

    if [ -n "$resolver" ]; then
        for domain in $domains; do
            printf '%s\n' "$domain" | grep -Eq '^([A-Za-z0-9][A-Za-z0-9-]*\.)+[A-Za-z][A-Za-z0-9-]*\.?$' || continue
            start_ms=$(monotonic_ms) || exit 1
            if dns_lookup "$domain" "$resolver" >/dev/null 2>&1; then
                ok=1
            else
                ok=0
            fi
            end_ms=$(monotonic_ms) || exit 1
            rows="$rows
              INSERT OR IGNORE INTO dns_metrics VALUES
              ($cycled,'$domain','$resolver',$((end_ms - start_ms)),$ok);"
        done
    fi

    finished=$(uptime_seconds) || exit 1
    sql "BEGIN IMMEDIATE;
      INSERT INTO network_cycles(at,duration_s,severe) VALUES ($at,$((finished - started)),0);
      $rows
      UPDATE network_cycles SET severe=EXISTS (
        SELECT 1 FROM network_metrics
        WHERE role <> 'desktop' AND cycle_id IN (SELECT id FROM network_cycles ORDER BY id DESC LIMIT 3)
          AND (loss_pct > 10 OR jitter_ms > 60)
        GROUP BY role,target HAVING count(*) = 3
      ) WHERE id=$cycled;
      INSERT INTO events(at,kind,message)
        SELECT $at,'SEVERE_LINK',$(sql_text "⚠️ [DEGRADAÇÃO SEVERA DE LINK] $location
Perda acima de 10% ou jitter acima de 60 ms no mesmo destino por três ciclos consecutivos.")
        WHERE (SELECT severe FROM network_cycles WHERE id=$cycled)=1
          AND coalesce((SELECT severe FROM network_cycles ORDER BY id DESC LIMIT 1 OFFSET 1),0)=0;
      COMMIT;" >/dev/null
)

network_loop() {
    while :; do
        started_up=$(uptime_seconds) || return 1
        network_cycle || printf '%s\n' 'Aegis: falha ao gravar o ciclo de rede.' >&2
        random=$(od -An -N2 -tu2 /dev/urandom | tr -d '[:space:]')
        [ -n "$random" ] || random=180
        ended_up=$(uptime_seconds) || return 1
        delay=$((1620 + random % 361 - (ended_up - started_up)))
        [ "$delay" -le 0 ] || sleep "$delay"
    done
}

network_message() {
    data=$(sql "SELECT 'Rede: ' || CASE severe WHEN 1 THEN 'degradação severa' ELSE 'sem degradação severa' END
        FROM network_cycles ORDER BY id DESC LIMIT 1;
      SELECT role || ' ' || target || ' | RTT ' || coalesce(rtt_ms || ' ms','—') ||
        ' | jitter ' || coalesce(jitter_ms || ' ms','—') || ' | perda ' || coalesce(loss_pct || '%','—')
        FROM network_metrics WHERE cycle_id=(SELECT max(id) FROM network_cycles) AND role <> 'desktop'
        ORDER BY role,target;
      SELECT 'DNS ' || domain || ' | ' || CASE ok WHEN 1 THEN elapsed_ms || ' ms' ELSE 'falha' END
        FROM dns_metrics WHERE cycle_id=(SELECT max(id) FROM network_cycles) ORDER BY domain;")
    printf 'Aegis-Sentinel | Último ciclo de rede\n%s\n' "${data:-Aguardando o primeiro ciclo.}"
}
