#!/system/bin/sh
# NetSentinel — Daemon Principal do Nó Remoto (Motorola Moto E7 Plus / LineageOS 24.0)
# Consumo típico: ~1-2 MB RAM, sem Python, sem GMS, proteção contra LMK e Doze.
# Modos de operação:
#   - NORMAL (tomada OK): heartbeat assinado (HMAC-SHA256) a cada 60s, limitador 60-75% bypass,
#     varredura passiva de espectro e atualização do Status Card no Telegram a cada 5 min.
#   - SOBREVIVÊNCIA (sem energia na tomada): mantém tela apagada, libera wakelock entre ciclos,
#     reduz cadência para 120s e acumula eventos na caixa-preta local até a luz voltar.

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -f "${SCRIPT_DIR}/lib_common.sh" ] && [ -f "${NS_ROOT:-/data/adb/netsentinel}/lib_common.sh" ]; then
    SCRIPT_DIR="${NS_ROOT:-/data/adb/netsentinel}"
fi
# shellcheck source=lib_common.sh
. "${SCRIPT_DIR}/lib_common.sh"
# shellcheck source=battery_limiter.sh
. "${SCRIPT_DIR}/battery_limiter.sh"
# shellcheck source=power_blackbox.sh
. "${SCRIPT_DIR}/power_blackbox.sh"
# shellcheck source=spectrum_auditor.sh
. "${SCRIPT_DIR}/spectrum_auditor.sh"
# shellcheck source=telegram_client.sh
. "${SCRIPT_DIR}/telegram_client.sh"

PIDFILE="${NS_ROOT}/netsentinel.pid"
LAST_CARD_TS=0
LAST_SPECTRUM_TS=0
LAST_THERMAL_ALERT_TS=0
HUB_FAIL_COUNT=0
NET_FAIL_COUNT=0

cleanup_on_exit() {
    wakelock_release
    rm -f "$PIDFILE"
    ns_log "INFO" "netsentinel_node: encerrado graciosamente."
    exit 0
}
trap cleanup_on_exit INT TERM

# Lê RSSI atual do link Wi-Fi (0 se desconectado)
read_wifi_rssi() {
    if command -v iw >/dev/null 2>&1; then
        r=$(iw wlan0 link 2>/dev/null | awk '/signal:/ { printf "%d", $2; exit }')
        if [ -n "$r" ]; then
            echo "$r"
            return 0
        fi
    fi
    r=$(dumpsys wifi 2>/dev/null | awk '/mWifiInfo / {
        if (match($0, /RSSI: -?[0-9]+/)) {
            s = substr($0, RSTART + 6, RLENGTH - 6)
            print s; exit
        }
    }')
    echo "${r:--127}"
}

# Verifica conectividade real com a internet (ICMP rápido com fallback)
check_internet_online() {
    if ping -c 1 -W 3 "$PING_TARGET_PRIMARY" >/dev/null 2>&1; then
        echo "1"
        return 0
    fi
    if ping -c 1 -W 3 "$PING_TARGET_SECONDARY" >/dev/null 2>&1; then
        echo "1"
        return 0
    fi
    echo "0"
}

# Emite heartbeat JSON assinado (HMAC-SHA256) para o Hub Nobara Linux
send_signed_heartbeat() {
    now_ts="$1"
    ac_online_bool="$2"
    batt_pct="$3"
    temp_c="$4"
    voltage_mv="$5"
    uptime_sec="$6"
    wifi_rssi="$7"
    limiter_action="$8"
    pending_blackbox_json="$9"
    spectrum_anomalies_json="${10}"
    lan_devices_json="${11:-[]}"

    [ -n "$HUB_ENDPOINT_URL" ] || return 2
    [ -n "$NETSENTINEL_HMAC_SECRET" ] || {
        ns_log "ERROR" "netsentinel_node: NETSENTINEL_HMAC_SECRET vazio; heartbeat abortado."
        return 1
    }

    esc_node=$(json_escape "$NETSENTINEL_NODE_ID")
    esc_loc=$(json_escape "$NETSENTINEL_LOCATION_LABEL")
    esc_act=$(json_escape "$limiter_action")

    payload="{\"node_id\":\"${esc_node}\",\"location\":\"${esc_loc}\",\"timestamp\":${now_ts},\"ac_online\":${ac_online_bool},\"battery_pct\":${batt_pct},\"temp_c\":${temp_c},\"voltage_mv\":${voltage_mv},\"uptime_sec\":${uptime_sec},\"wifi_rssi\":${wifi_rssi},\"battery_mode\":\"${esc_act}\",\"pending_blackbox_events\":${pending_blackbox_json},\"spectrum_anomalies\":${spectrum_anomalies_json},\"lan_devices\":${lan_devices_json}}"

    sig=$(hmac_sha256_hex "$NETSENTINEL_HMAC_SECRET" "$payload")

    http_code=$(curl -sS -o /dev/null -w "%{http_code}" \
        --connect-timeout 4 \
        --max-time "${CURL_TIMEOUT_SEC:-8}" \
        -X POST "$HUB_ENDPOINT_URL" \
        -H "Content-Type: application/json" \
        -H "X-NetSentinel-Node: ${NETSENTINEL_NODE_ID}" \
        -H "X-NetSentinel-Timestamp: ${now_ts}" \
        -H "X-NetSentinel-Signature: sha256=${sig}" \
        --data-binary "$payload" 2>/dev/null || echo "000")

    case "$http_code" in
        200|201|202|204)
            return 0
            ;;
        *)
            ns_log "WARN" "netsentinel_node: falha ao enviar heartbeat ao Hub (HTTP ${http_code})"
            return 1
            ;;
    esac
}

# Executa 1 ciclo completo do sentinela
run_single_cycle() {
    ns_load_env
    ns_ensure_dirs
    wakelock_acquire

    now_ts=$(ns_now_epoch)
    now_iso=$(ns_now_iso)
    uptime_sec=$(ns_uptime_sec)

    # 1. Saúde da bateria (60-75% bypass + corte térmico >= 40.0 C)
    limiter_action=$(apply_battery_policy)
    read_battery_metrics

    # 2. Rede e RSSI (evita ping custoso em queda elétrica se Wi-Fi já estiver fora)
    wifi_rssi=$(read_wifi_rssi)
    if [ "$wifi_rssi" -le -120 ] && [ "$BATT_AC_PRESENT" -eq 0 ]; then
        net_online=0
    else
        net_online=$(check_internet_online)
    fi

    # 3. Caixa-preta de queda elétrica vs queda de ISP
    pwr_transition=$(power_blackbox_step "$now_ts" "$BATT_AC_PRESENT" "$net_online" "$BATT_PCT" "$BATT_VOLTAGE_MV" "$BATT_TEMP_C")

    # 4. Radar passivo de espectro (opera tanto online quanto offline salvando na caixa-preta)
    spec_elapsed=$((now_ts - LAST_SPECTRUM_TS))
    if [ "$spec_elapsed" -ge "$SPECTRUM_SCAN_INTERVAL_SEC" ]; then
        run_spectrum_audit "$now_ts" >/dev/null 2>&1 || true
        LAST_SPECTRUM_TS="$now_ts"
    fi

    # 5. Transmissão quando online (Hub assinado + Redundância Telegram)
    if [ "$net_online" -eq 1 ]; then
        NET_FAIL_COUNT=0
        ac_bool="true"
        [ "$BATT_AC_PRESENT" -eq 0 ] && ac_bool="false"

        pending_bb=$(get_pending_events_json_array)
        pending_sp=$(get_pending_anomalies_json_array)
        lan_devs=$(collect_lan_devices_json 2>/dev/null || echo "[]")

        hub_ok=0
        if [ -n "$HUB_ENDPOINT_URL" ]; then
            # Se o Hub estiver inacessível repetidamente, espaça tentativas para cada 5 min (economiza bateria/tempo)
            should_try_hub=1
            if [ "$HUB_FAIL_COUNT" -ge 3 ] && [ $((now_ts % 300)) -ge "$HEARTBEAT_INTERVAL_SEC" ]; then
                should_try_hub=0
            fi

            if [ "$should_try_hub" -eq 1 ]; then
                if send_signed_heartbeat "$now_ts" "$ac_bool" "$BATT_PCT" "$BATT_TEMP_C" "$BATT_VOLTAGE_MV" "$uptime_sec" "$wifi_rssi" "$limiter_action" "$pending_bb" "$pending_sp" "$lan_devs"; then
                    hub_ok=1
                    HUB_FAIL_COUNT=0
                else
                    HUB_FAIL_COUNT=$((HUB_FAIL_COUNT + 1))
                fi
            fi
        else
            hub_ok=2
        fi

        # Redundância direta via Telegram (funciona 24/7 mesmo com o PC em Campina Grande desligado)
        tg_ok=0
        if tg_is_configured; then
            tg_ok=1
            # Se o Hub NÃO recebeu (hub_ok != 1), o nó despacha diretamente para o Telegram como contingência
            if [ "$hub_ok" -ne 1 ]; then
                if [ -s "$PENDING_EVENTS_FILE" ]; then
                    tg_flush_critical_events "$PENDING_EVENTS_FILE" || tg_ok=0
                fi
                if [ -s "$ANOMALIES_PENDING_FILE" ]; then
                    tg_flush_critical_events "$ANOMALIES_PENDING_FILE" || tg_ok=0
                fi
            fi

            # Alerta térmico imediato com cooldown de 15 min (apenas se Hub não avisou)
            if [ "$limiter_action" = "THERMAL_CUT" ] && [ $((now_ts - LAST_THERMAL_ALERT_TS)) -ge 900 ]; then
                if [ "$hub_ok" -ne 1 ]; then
                    tg_send_message "🌡️ [NetSentinel] Alerta de Temperatura — ${NETSENTINEL_LOCATION_LABEL}: A bateria atingiu ${BATT_TEMP_C} °C. O carregamento foi pausado por segurança até o aparelho esfriar." >/dev/null 2>&1 || true
                fi
                LAST_THERMAL_ALERT_TS="$now_ts"
            fi

            # Atualiza Status Card fixado a cada STATUS_CARD_INTERVAL_SEC (padrão 5 min)
            if [ $((now_ts - LAST_CARD_TS)) -ge "$STATUS_CARD_INTERVAL_SEC" ]; then
                if tg_update_status_card "$now_iso" "$BATT_AC_PRESENT" "$BATT_PCT" "$BATT_TEMP_C" "$wifi_rssi" "$limiter_action" "$uptime_sec"; then
                    LAST_CARD_TS="$now_ts"
                fi
            fi
        fi

        # Limpa filas pendentes se o Hub registrou com sucesso OU se o Telegram entregou com sucesso
        if [ "$hub_ok" -eq 1 ] || [ "$tg_ok" -eq 1 ]; then
            clear_pending_events
            clear_pending_anomalies
        fi
    else
        # Se energia está normal na tomada mas internet caiu por 5 ciclos seguidos, acorda o wlan0
        if [ "$BATT_AC_PRESENT" -eq 1 ]; then
            NET_FAIL_COUNT=$((NET_FAIL_COUNT + 1))
            if [ "$NET_FAIL_COUNT" -ge 5 ]; then
                cmd wifi set-wifi-enabled enabled >/dev/null 2>&1 || svc wifi enable >/dev/null 2>&1 || true
                NET_FAIL_COUNT=0
            fi
        fi

        # Modo de Sobrevivência (Offline / Sem Energia): garante tela desligada para máxima autonomia
        if [ "$BATT_AC_PRESENT" -eq 0 ]; then
            if dumpsys power 2>/dev/null | grep -q "mWakefulness=Awake"; then
                input keyevent 223 >/dev/null 2>&1 || true
            fi
        fi
    fi

    # Grava snapshot de telemetria local para inspeção rápida via ADB
    atomic_write "${NS_STATE_DIR}/last_cycle.json" "{\"ts\":${now_ts},\"iso\":\"${now_iso}\",\"ac_present\":${BATT_AC_PRESENT},\"net_online\":${net_online},\"battery_pct\":${BATT_PCT},\"temp_c\":${BATT_TEMP_C},\"voltage_mv\":${BATT_VOLTAGE_MV},\"wifi_rssi\":${wifi_rssi},\"limiter_action\":\"${limiter_action}\",\"transition\":\"${pwr_transition}\"}"

    wakelock_release

    # Retorna intervalo do próximo ciclo (Normal vs Sobrevivência)
    if [ "$BATT_AC_PRESENT" -eq 0 ]; then
        NEXT_SLEEP="$SURVIVAL_POLL_INTERVAL_SEC"
    else
        NEXT_SLEEP="$HEARTBEAT_INTERVAL_SEC"
    fi
}

main() {
    ns_load_env
    ns_ensure_dirs
    protect_from_lmk

    if [ "${1:-}" = "--once" ]; then
        run_single_cycle
        cat "${NS_STATE_DIR}/last_cycle.json"
        echo ""
        exit 0
    fi

    atomic_write "$PIDFILE" "$$"
    ns_log "INFO" "netsentinel_node: iniciado (PID=$$, node=${NETSENTINEL_NODE_ID}, normal=${HEARTBEAT_INTERVAL_SEC}s, survival=${SURVIVAL_POLL_INTERVAL_SEC}s)"

    while true; do
        NEXT_SLEEP="$HEARTBEAT_INTERVAL_SEC"
        run_single_cycle
        sleep "$NEXT_SLEEP"
    done
}

if [ "${0##*/}" = "netsentinel_node.sh" ]; then
    main "$@"
fi
