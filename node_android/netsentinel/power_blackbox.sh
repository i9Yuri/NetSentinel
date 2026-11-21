#!/system/bin/sh
# NetSentinel — Caixa-Preta Atômica de Queda de Energia vs Queda de ISP
# Registra transições em /data/adb/netsentinel/blackbox.log (JSONL durável + fsync)
# e estado corrente em /data/adb/netsentinel/state/power_state.env (tmp + fsync + mv).

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -f "${SCRIPT_DIR}/lib_common.sh" ] && [ -f "${NS_ROOT:-/data/adb/netsentinel}/lib_common.sh" ]; then
    SCRIPT_DIR="${NS_ROOT:-/data/adb/netsentinel}"
fi
# shellcheck source=lib_common.sh
. "${SCRIPT_DIR}/lib_common.sh"

BLACKBOX_LOG="${BLACKBOX_LOG:-${NS_ROOT}/blackbox.log}"
PENDING_EVENTS_FILE="${PENDING_EVENTS_FILE:-${NS_STATE_DIR}/pending_events.jsonl}"
POWER_STATE_FILE="${POWER_STATE_FILE:-${NS_STATE_DIR}/power_state.env}"

# Inicializa ou carrega estado anterior
_load_power_state() {
    PWR_LAST_AC=1
    PWR_LAST_NET=1
    PWR_OUTAGE_ACTIVE=0
    PWR_OUTAGE_START_TS=0
    PWR_OUTAGE_START_BATT=0
    PWR_OUTAGE_START_MV=0
    PWR_NET_DOWN_ACTIVE=0
    PWR_NET_DOWN_START_TS=0
    PWR_NET_HAD_AC_DROP=0
    PWR_LOW_BATT_SENT=0

    if [ -r "$POWER_STATE_FILE" ]; then
        # shellcheck disable=SC1090
        . "$POWER_STATE_FILE"
    fi
}

_save_power_state() {
    atomic_write "$POWER_STATE_FILE" "PWR_LAST_AC=${PWR_LAST_AC}
PWR_LAST_NET=${PWR_LAST_NET}
PWR_OUTAGE_ACTIVE=${PWR_OUTAGE_ACTIVE}
PWR_OUTAGE_START_TS=${PWR_OUTAGE_START_TS}
PWR_OUTAGE_START_BATT=${PWR_OUTAGE_START_BATT}
PWR_OUTAGE_START_MV=${PWR_OUTAGE_START_MV}
PWR_NET_DOWN_ACTIVE=${PWR_NET_DOWN_ACTIVE}
PWR_NET_DOWN_START_TS=${PWR_NET_DOWN_START_TS}
PWR_NET_HAD_AC_DROP=${PWR_NET_HAD_AC_DROP}
PWR_LOW_BATT_SENT=${PWR_LOW_BATT_SENT}
"
}

# Registra evento forense na caixa-preta local e na fila pendente de envio ao Hub/Telegram
record_blackbox_event() {
    ev_json="$1"
    atomic_append_line "$BLACKBOX_LOG" "$ev_json"
    atomic_append_line "$PENDING_EVENTS_FILE" "$ev_json"
}

# Conta quantos eventos pendentes existem na caixa-preta
count_pending_events() {
    if [ -r "$PENDING_EVENTS_FILE" ]; then
        wc -l < "$PENDING_EVENTS_FILE" 2>/dev/null | tr -d ' '
    else
        echo "0"
    fi
}

# Atualiza a máquina de estados com a amostra atual:
# Args: <now_ts> <ac_present:0|1> <net_online:0|1> <batt_pct> <voltage_mv> <temp_c>
# Saída stdout: evento de transição principal (NONE | POWER_LOST | POWER_RESTORED_REAL | ISP_RESTORED_ONLY | LOW_BATT_OUTAGE)
power_blackbox_step() {
    now_ts="$1"
    ac_now="$2"
    net_now="$3"
    batt_pct="$4"
    voltage_mv="$5"
    temp_c="$6"

    ns_load_env
    ns_ensure_dirs
    _load_power_state

    transition="NONE"

    # --- 1. Rastreamento de Energia AC (Tomada) ---
    if [ "$ac_now" -eq 0 ] && [ "$PWR_OUTAGE_ACTIVE" -eq 0 ]; then
        # Queda elétrica detectada neste instante!
        PWR_OUTAGE_ACTIVE=1
        PWR_OUTAGE_START_TS="$now_ts"
        PWR_OUTAGE_START_BATT="$batt_pct"
        PWR_OUTAGE_START_MV="$voltage_mv"
        PWR_NET_HAD_AC_DROP=1
        PWR_LOW_BATT_SENT=0

        ev="{\"event\":\"POWER_LOST\",\"ts\":${now_ts},\"ac_online\":false,\"battery_pct\":${batt_pct},\"voltage_mv\":${voltage_mv},\"temp_c\":${temp_c}}"
        record_blackbox_event "$ev"
        ns_log "WARN" "power_blackbox: QUEDA DE ENERGIA DETECTADA! ts=${now_ts} batt=${batt_pct}% mv=${voltage_mv}"
        transition="POWER_LOST"
    elif [ "$ac_now" -eq 1 ] && [ "$PWR_OUTAGE_ACTIVE" -eq 1 ]; then
        # Energia elétrica retornou!
        outage_sec=$((now_ts - PWR_OUTAGE_START_TS))
        [ "$outage_sec" -lt 0 ] && outage_sec=0
        fridge_risk="false"
        if [ "$outage_sec" -ge "$FRIDGE_RISK_OUTAGE_SEC" ]; then
            fridge_risk="true"
        fi

        ev="{\"event\":\"POWER_RESTORED\",\"classification\":\"REAL_POWER_OUTAGE\",\"power_lost_ts\":${PWR_OUTAGE_START_TS},\"power_restored_ts\":${now_ts},\"outage_duration_sec\":${outage_sec},\"battery_start_pct\":${PWR_OUTAGE_START_BATT},\"battery_end_pct\":${batt_pct},\"voltage_start_mv\":${PWR_OUTAGE_START_MV},\"voltage_end_mv\":${voltage_mv},\"fridge_risk\":${fridge_risk}}"
        record_blackbox_event "$ev"
        ns_log "INFO" "power_blackbox: ENERGIA RESTAURADA! duracao=${outage_sec}s tipo=REAL_POWER_OUTAGE fridge_risk=${fridge_risk}"

        PWR_OUTAGE_ACTIVE=0
        PWR_OUTAGE_START_TS=0
        PWR_LOW_BATT_SENT=0
        # Se a rede também estava marcada como fora durante a falta de luz, já foi classificada como falta de luz
        PWR_NET_HAD_AC_DROP=1
        transition="POWER_RESTORED_REAL"
    fi

    # --- 2. Rastreamento de Internet (ISP) desacoplado de AC ---
    if [ "$net_now" -eq 0 ] && [ "$PWR_NET_DOWN_ACTIVE" -eq 0 ]; then
        PWR_NET_DOWN_ACTIVE=1
        PWR_NET_DOWN_START_TS="$now_ts"
        if [ "$ac_now" -eq 0 ] || [ "$PWR_OUTAGE_ACTIVE" -eq 1 ]; then
            PWR_NET_HAD_AC_DROP=1
        else
            PWR_NET_HAD_AC_DROP=0
        fi
    elif [ "$net_now" -eq 0 ] && [ "$PWR_NET_DOWN_ACTIVE" -eq 1 ]; then
        if [ "$ac_now" -eq 0 ] || [ "$PWR_OUTAGE_ACTIVE" -eq 1 ]; then
            PWR_NET_HAD_AC_DROP=1
        fi
    elif [ "$net_now" -eq 1 ] && [ "$PWR_NET_DOWN_ACTIVE" -eq 1 ]; then
        net_outage_sec=$((now_ts - PWR_NET_DOWN_START_TS))
        [ "$net_outage_sec" -lt 0 ] && net_outage_sec=0
        # Só gera relatório ISP_ONLY_OUTAGE se a tomada permaneceu ONLINE durante 100% da queda de rede!
        if [ "$PWR_NET_HAD_AC_DROP" -eq 0 ] && [ "$net_outage_sec" -ge 30 ]; then
            ev="{\"event\":\"ISP_RESTORED\",\"classification\":\"ISP_ONLY_OUTAGE\",\"net_lost_ts\":${PWR_NET_DOWN_START_TS},\"net_restored_ts\":${now_ts},\"outage_duration_sec\":${net_outage_sec},\"ac_stayed_online\":true,\"battery_pct\":${batt_pct}}"
            record_blackbox_event "$ev"
            ns_log "INFO" "power_blackbox: INTERNET RESTAURADA (APENAS ISP)! duracao=${net_outage_sec}s tomada=OK"
            if [ "$transition" = "NONE" ]; then
                transition="ISP_RESTORED_ONLY"
            fi
        fi
        PWR_NET_DOWN_ACTIVE=0
        PWR_NET_DOWN_START_TS=0
        PWR_NET_HAD_AC_DROP=0
    fi

    # --- 3. Alerta de bateria crítica durante queda prolongada ---
    if [ "$PWR_OUTAGE_ACTIVE" -eq 1 ] && [ "$batt_pct" -le "$BATTERY_LOW_ALERT_PCT" ] && [ "$PWR_LOW_BATT_SENT" -eq 0 ]; then
        PWR_LOW_BATT_SENT=1
        ev="{\"event\":\"LOW_BATTERY_OUTAGE\",\"ts\":${now_ts},\"battery_pct\":${batt_pct},\"voltage_mv\":${voltage_mv}}"
        record_blackbox_event "$ev"
        if [ "$transition" = "NONE" ]; then
            transition="LOW_BATT_OUTAGE"
        fi
    fi

    PWR_LAST_AC="$ac_now"
    PWR_LAST_NET="$net_now"
    _save_power_state

    echo "$transition"
}

# Retorna os eventos pendentes como JSON array compacto (para envio no heartbeat)
get_pending_events_json_array() {
    if [ ! -s "$PENDING_EVENTS_FILE" ]; then
        echo "[]"
        return 0
    fi
    awk 'BEGIN { printf "[" }
         NR > 1 { printf "," }
         { printf "%s", $0 }
         END { printf "]" }' "$PENDING_EVENTS_FILE"
}

# Limpa fila pendente de forma atômica após confirmação de entrega (mantém histórico eterno em blackbox.log)
clear_pending_events() {
    atomic_write "$PENDING_EVENTS_FILE" ""
}

if [ "${0##*/}" = "power_blackbox.sh" ]; then
    case "${1:-}" in
        --step)
            shift
            power_blackbox_step "$@"
            ;;
        --pending)
            get_pending_events_json_array
            echo ""
            ;;
        --clear-pending)
            clear_pending_events
            ;;
        *)
            echo "Uso: $0 --step <ts> <ac:0|1> <net:0|1> <batt_pct> <voltage_mv> <temp_c> | --pending | --clear-pending" >&2
            exit 2
            ;;
    esac
fi
