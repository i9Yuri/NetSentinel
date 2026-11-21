#!/system/bin/sh
# Carregado por aegis_daemon.sh. Os estados elétricos vêm do PMI632.
. "$base/modules/smart_profile.sh"
power_root=${AEGIS_POWER_ROOT:-/sys/class/power_supply}

read_node() {
    reading=''
    if [ -r "$power_root/$1" ]; then
        { IFS= read -r reading < "$power_root/$1"; } 2>/dev/null || reading=''
    fi
    case "${reading#-}" in ''|*[!0-9]*) reading=NULL ;; esac
    case "$reading" in
        NULL|0) ;;
        -*|[1-9]*) [ "${#reading}" -le 10 ] || reading=NULL ;;
        *) reading=NULL ;;
    esac
}

is_external_power_present() {
    read_node usb/present; usb_present=$reading
    read_node usb/online; usb_online=$reading
    read_node pc_port/online; pc_online=$reading
    read_node usb/voltage_now; usb_uv=$reading
    if [ "$usb_present" = 1 ] || [ "$usb_online" = 1 ] || [ "$pc_online" = 1 ] ||
       { [ "$usb_uv" != NULL ] && [ "$usb_uv" -ge 3500000 ]; }; then
        return 1
    fi
    if [ "$usb_present" = 0 ] || [ "$usb_online" = 0 ] || [ "$pc_online" = 0 ] ||
       [ "$usb_uv" != NULL ]; then
        return 0
    fi
    return 2
}

read_battery() {
    read_node battery/capacity; battery_pct=$reading
    read_node battery/temp; temp_tenths=$reading
    read_node battery/voltage_now; battery_uv=$reading
    read_node battery/current_now; battery_ua=$reading
}

power_snapshot_sql() {
    printf "INSERT OR REPLACE INTO power_status VALUES (1,%s,%s,%s,%s,%s,%s,%s);" \
        "$1" "$2" "$battery_pct" "$temp_tenths" "$battery_uv" "$battery_ua" "$usb_uv"
}

power_record_transition() (
    if [ "$candidate" = 0 ]; then
        read_node battery/charge_full
        if [ "$reading" = NULL ] || [ "$reading" -le 0 ]; then
            read_node battery/charge_full_design
        fi
        full_ua_h=$reading
        if [ "$full_ua_h" = NULL ] || [ "$full_ua_h" -le 0 ]; then full_ua_h=4000000; fi
        autonomy=$(awk -v pct="$battery_pct" -v full="$full_ua_h" -v ua="$battery_ua" \
            'BEGIN { if (ua < 0) ua = -ua; if (ua+0 >= 1000 && pct != "NULL")
               printf "Autonomia restante estimada: ~%.1fh", full*pct/(100*ua);
               else printf "Autonomia indisponível" }')
        level='indisponível'
        [ "$battery_pct" = NULL ] || level="$battery_pct%"
        cut_time=$(date -d "@$candidate_at" +%T 2>/dev/null) || cut_time=$(date +%T)
        message="⚠️ [ALERTA ENERGIA] $location
• Rede Elétrica: DESCONECTADA (Queda de luz detectada)
• Horário do corte: $cut_time
• Nível da bateria do Sentinela: $level ($autonomy)
• Status da Workstation: Desligada / Sem energia (presumido)"
        event=POWER_LOST
        duration=NULL
    else
        if [ -n "$lost_since_uptime" ]; then
            duration=$((candidate_since - lost_since_uptime))
        else
            lost_at=$(sql "SELECT at FROM events WHERE kind='POWER_LOST' ORDER BY id DESC LIMIT 1;") || exit 1
            duration=$((candidate_at - ${lost_at:-$candidate_at}))
        fi
        [ "$duration" -ge 0 ] || duration=0
        event=POWER_RESTORED
        message="✅ [ENERGIA RESTABELECIDA]
• Duração total do apagão: $((duration / 60)) minutos e $((duration % 60)) segundos
• Sistema em operação normal"
    fi
    sql "BEGIN IMMEDIATE;
      INSERT INTO events(at,kind,duration_s,message) VALUES
        ($candidate_at,'$event',$duration,$(sql_text "$message"));
      $(power_snapshot_sql "$now" "$candidate")
      COMMIT;" >/dev/null
)

dispatch_power_profile() {
    [ -n "$profile_pending" ] || return 0
    # Mantém apenas o perfil mais recente enquanto a aplicação anterior termina.
    if [ -n "$profile_worker" ] && kill -0 "$profile_worker" 2>/dev/null; then
        return 0
    fi
    {
        sync_profile_with_power "$profile_pending" ||
            printf 'Aegis: falha ao aplicar perfil de energia (AC=%s).\n' "$profile_pending" >&2
    } 9>&- >/dev/null &
    profile_worker=$!
    profile_pending=''
}

queue_power_profile() {
    [ "$1" != "$profile_requested" ] || return 0
    profile_requested=$1
    profile_pending=$1
    dispatch_power_profile
}

power_transition() {
    power_record_transition || return 1
    queue_power_profile "$candidate"
}

power_loop() {
    stable=$(sql 'SELECT ac FROM power_status WHERE id=1;') || return 1
    candidate='' lost_since_uptime='' last_snapshot=-60
    profile_pending='' profile_requested='' profile_worker=''
    while :; do
        now_up=$(uptime_seconds) || return 1
        is_external_power_present
        current=$?
        if [ "$current" != 2 ] && { [ -z "$profile_requested" ] || [ "$current" = "$stable" ]; }; then
            queue_power_profile "$current"
        fi
        if [ "$current" = 2 ] || [ "$current" = "$stable" ]; then
            candidate=''
        elif [ "$current" != "$candidate" ]; then
            candidate=$current
            candidate_since=$now_up
            candidate_at=$(date +%s)
        fi

        threshold=5
        if [ "$candidate" = 1 ]; then
            if [ "$stable" = 0 ]; then threshold=10; else threshold=0; fi
        fi
        if [ -n "$candidate" ] && [ "$((now_up - candidate_since))" -ge "$threshold" ]; then
            now=$(date +%s)
            read_battery
            if [ -z "$stable" ] && [ "$candidate" = 1 ]; then
                sql "$(power_snapshot_sql "$now" "$candidate")" >/dev/null || { sleep 1; continue; }
            else
                power_transition || { sleep 1; continue; }
            fi
            if [ "$candidate" = 0 ]; then lost_since_uptime=$candidate_since; fi
            stable=$candidate
            candidate=''
            last_snapshot=$now_up
        elif [ "$candidate" = '' ] && [ "$current" != 2 ] &&
             [ "$((now_up - last_snapshot))" -ge 60 ]; then
            now=$(date +%s)
            read_battery
            sql "$(power_snapshot_sql "$now" "$stable")" >/dev/null && last_snapshot=$now_up
        fi
        dispatch_power_profile
        sleep 1
    done
}
