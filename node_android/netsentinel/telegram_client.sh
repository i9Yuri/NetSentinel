#!/system/bin/sh
# NetSentinel — Cliente Direto Telegram Bot API no Moto E7 Plus (Custo R$ 0,00)
# Redundância para PC desligado: envia alertas críticos diretamente e mantém
# um Status Card fixado atualizado in-place via editMessageText.

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -f "${SCRIPT_DIR}/lib_common.sh" ] && [ -f "${NS_ROOT:-/data/adb/netsentinel}/lib_common.sh" ]; then
    SCRIPT_DIR="${NS_ROOT:-/data/adb/netsentinel}"
fi
# shellcheck source=lib_common.sh
. "${SCRIPT_DIR}/lib_common.sh"

TG_STATE_FILE="${TG_STATE_FILE:-${NS_STATE_DIR}/telegram_card.state}"

tg_is_configured() {
    [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ] && return 0
    ns_load_env
    [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]
}

TG_KEYBOARD_JSON='{"keyboard":[[{"text":"⚡ Status do Apê"},{"text":"❄️ Geladeira & Luz"}],[{"text":"🖥️ Ligar Workstation (WoL)"},{"text":"📊 Relatório 24h"}],[{"text":"🌐 Sinal Wi-Fi"},{"text":"🛡️ Dispositivos na Rede"}],[{"text":"🔋 Bateria"},{"text":"📋 Histórico"}]],"resize_keyboard":true,"is_persistent":true}'

# Envia mensagem simples ao chat configurado; retorna 0 em sucesso
tg_send_message() {
    text="$1"
    tg_is_configured || return 1

    resp=$(curl -sS --max-time "${CURL_TIMEOUT_SEC:-12}" \
        -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${text}" \
        --data-urlencode "reply_markup=${TG_KEYBOARD_JSON}" \
        -d "disable_web_page_preview=true" 2>/dev/null) || return 1

    printf '%s' "$resp" | grep -q '"ok":true' || return 1
    printf '%s' "$resp"
    return 0
}

# Converte modo de bateria técnico para texto claro e legível
format_limiter_mode_human() {
    case "$1" in
        BYPASS_HOLD) echo "Bypass ativo (poupa bateria)" ;;
        CHARGE_ON) echo "Recarregando até 75%" ;;
        GENTLE_DISCHARGE) echo "Descarga suave de proteção" ;;
        THERMAL_CUT) echo "Pausa térmica preventiva" ;;
        HYSTERESIS_KEEP) echo "Faixa protegida (60%-75%)" ;;
        ON_BATTERY) echo "Operando na bateria" ;;
        *) echo "$1" ;;
    esac
}

# Converte RSSI de Wi-Fi para indicador intuitivo de qualidade
format_wifi_quality() {
    rssi="$1"
    if [ "$rssi" -ge -55 ]; then
        echo "Excelente (${rssi} dBm)"
    elif [ "$rssi" -ge -67 ]; then
        echo "Bom (${rssi} dBm)"
    elif [ "$rssi" -ge -78 ]; then
        echo "Regular (${rssi} dBm)"
    elif [ "$rssi" -gt -120 ]; then
        echo "Fraco (${rssi} dBm)"
    else
        echo "Desconectado"
    fi
}

# Obtém data e hora no fuso horário local brasileiro
ns_local_datetime() {
    dt=$(TZ="${LOCAL_TIMEZONE:-America/Fortaleza}" date +%d-%m-%Y_%H:%M:%S 2>/dev/null | tr '-' '/' | tr '_' ' ')
    if [ -n "$dt" ]; then
        printf '%s' "$dt"
    else
        printf '%s' "$1"
    fi
}

# Atualiza o Status Card fixado (cria e fixa se ainda não existir message_id salvo)
# Args: <now_iso> <ac_present> <batt_pct> <temp_c> <wifi_rssi> <limiter_action> <uptime_sec>
tg_update_status_card() {
    now_iso="$1"
    ac_present="$2"
    batt_pct="$3"
    temp_c="$4"
    wifi_rssi="$5"
    limiter_action="$6"
    uptime_sec="$7"

    tg_is_configured || return 1

    card_msg_id=""
    if [ -r "$TG_STATE_FILE" ]; then
        card_msg_id=$(head -n 1 "$TG_STATE_FILE" 2>/dev/null | tr -d ' \r\n')
    fi

    ac_label="Tomada ligada (energia OK)"
    [ "$ac_present" -eq 0 ] && ac_label="Sem energia na tomada (na bateria)"

    mode_label=$(format_limiter_mode_human "$limiter_action")
    wifi_label=$(format_wifi_quality "$wifi_rssi")
    uptime_human=$(format_duration_human "$uptime_sec")
    local_time=$(ns_local_datetime "$now_iso")

    card_text="🛡️ NetSentinel — Vigia do Apê
📍 Local: ${NETSENTINEL_LOCATION_LABEL}
⚡ Energia: ${ac_label}
🔋 Bateria: ${batt_pct}% (${temp_c}°C | ${mode_label})
📶 Sinal Wi-Fi: ${wifi_label}
⏱️ Sistema ativo: ${uptime_human}
🕒 Última verificação: ${local_time}"

    if [ -n "$card_msg_id" ]; then
        resp=$(curl -sS --max-time "${CURL_TIMEOUT_SEC:-12}" \
            -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/editMessageText" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "message_id=${card_msg_id}" \
            --data-urlencode "text=${card_text}" 2>/dev/null || true)

        if printf '%s' "$resp" | grep -qE '"ok":true|message is not modified'; then
            return 0
        fi
        # Se a mensagem foi apagada no chat, cria uma nova abaixo
    fi

    resp=$(tg_send_message "$card_text") || return 1
    new_id=$(printf '%s' "$resp" | sed -n 's/.*"message_id":[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -n 1)
    if [ -n "$new_id" ]; then
        atomic_write "$TG_STATE_FILE" "$new_id"
        curl -sS --max-time "${CURL_TIMEOUT_SEC:-12}" \
            -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/pinChatMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "message_id=${new_id}" \
            -d "disable_notification=true" >/dev/null 2>&1 || true
    fi
    return 0
}

# Formata duração em segundos para texto amigável
format_duration_human() {
    secs="$1"
    d=$((secs / 86400))
    rem=$((secs % 86400))
    h=$((rem / 3600))
    m=$(((rem % 3600) / 60))
    s=$((rem % 60))
    if [ "$d" -gt 0 ]; then
        printf '%d dia(s), %dh %dm' "$d" "$h" "$m"
    elif [ "$h" -gt 0 ]; then
        printf '%dh %dm' "$h" "$m"
    elif [ "$m" -gt 0 ]; then
        printf '%dm %ds' "$m" "$s"
    else
        printf '%ds' "$s"
    fi
}

# Descarrega alertas forenses pendentes diretamente para o Telegram (quando online)
tg_flush_critical_events() {
    events_file="$1"
    tg_is_configured || return 1
    [ -s "$events_file" ] || return 0

    all_ok=0
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        ev_type=$(printf '%s' "$line" | sed -n 's/.*"event":"\([^"]*\)".*/\1/p')
        msg=""
        case "$ev_type" in
            POWER_LOST)
                bpct=$(printf '%s' "$line" | sed -n 's/.*"battery_pct":\([0-9-]*\).*/\1/p')
                msg="🔌 [NetSentinel] Tomada sem energia! — ${NETSENTINEL_LOCATION_LABEL}

O vigia detectou que a tomada parou de fornecer energia.
O aparelho passou a operar na bateria interna (${bpct}%).
Se o Wi-Fi continuar ativo, você continuará recebendo o status normalmente."
                ;;
            POWER_RESTORED)
                dur=$(printf '%s' "$line" | sed -n 's/.*"outage_duration_sec":\([0-9]*\).*/\1/p')
                b_start=$(printf '%s' "$line" | sed -n 's/.*"battery_start_pct":\([0-9-]*\).*/\1/p')
                b_end=$(printf '%s' "$line" | sed -n 's/.*"battery_end_pct":\([0-9-]*\).*/\1/p')
                fridge=$(printf '%s' "$line" | sed -n 's/.*"fridge_risk":\([a-z]*\).*/\1/p')
                dur_h=$(format_duration_human "${dur:-0}")
                warn_line=""
                [ "$fridge" = "true" ] && warn_line="

⚠️ ATENÇÃO: A falta de luz durou mais de 4 horas. Verifique os alimentos na geladeira e congelador!"
                msg="⚡ [NetSentinel] A luz voltou! — ${NETSENTINEL_LOCATION_LABEL}

Status: Queda de energia elétrica confirmada no imóvel.
Tempo total sem luz: ${dur_h}
Nível da bateria: de ${b_start}% para ${b_end}%${warn_line}"
                ;;
            ISP_RESTORED)
                dur=$(printf '%s' "$line" | sed -n 's/.*"outage_duration_sec":\([0-9]*\).*/\1/p')
                dur_h=$(format_duration_human "${dur:-0}")
                msg="🌐 [NetSentinel] A internet voltou! — ${NETSENTINEL_LOCATION_LABEL}

Status: Apenas queda do provedor de internet.
A energia elétrica na tomada permaneceu 100% normal (nenhum risco para a geladeira).
Tempo total sem conexão: ${dur_h}"
                ;;
            SPECTRUM_ANOMALY)
                mac=$(printf '%s' "$line" | sed -n 's/.*"mac":"\([^"]*\)".*/\1/p')
                vendor=$(printf '%s' "$line" | sed -n 's/.*"vendor":"\([^"]*\)".*/\1/p')
                src=$(printf '%s' "$line" | sed -n 's/.*"source":"\([^"]*\)".*/\1/p')
                lbl=$(printf '%s' "$line" | sed -n 's/.*"label":"\([^"]*\)".*/\1/p')
                if [ "$src" = "lan" ]; then
                    ip_part=$(printf '%s' "$lbl" | sed 's/^LAN://')
                    cls_info=$(classify_device "$mac" "$ip_part" "$vendor" "$lbl")
                    cat_ico=$(printf '%s' "$cls_info" | awk -F'\t' '{print $2}')
                    cat_name=$(printf '%s' "$cls_info" | awk -F'\t' '{print $3}')
                    msg="⚠️ [NetSentinel] Novo dispositivo conectado na rede do apê!
📍 Local: ${NETSENTINEL_LOCATION_LABEL}

${cat_ico} Identificação: ${cat_name}
Fabricante: ${vendor}
Endereço IP: ${ip_part}
Endereço físico (MAC): ${mac}

💡 Toque no botão 🛡️ Dispositivos na Rede para ver a lista completa."
                else
                    msg="📡 [NetSentinel] Dispositivo próximo detectado
📍 Local: ${NETSENTINEL_LOCATION_LABEL}

Identificação: ${lbl}
Fabricante: ${vendor}
Endereço físico (MAC): ${mac}"
                fi
                ;;
            LOW_BATTERY_OUTAGE)
                bpct=$(printf '%s' "$line" | sed -n 's/.*"battery_pct":\([0-9-]*\).*/\1/p')
                msg="🔋 [NetSentinel] Bateria do vigia baixa! — ${NETSENTINEL_LOCATION_LABEL}

A energia ainda não voltou e a bateria do aparelho caiu para ${bpct}%.
O sistema economizará bateria para preservar os registros até a luz voltar."
                ;;
        esac

        if [ -n "$msg" ]; then
            if ! tg_send_message "$msg" >/dev/null 2>&1; then
                all_ok=1
            fi
        fi
    done < "$events_file"

    return "$all_ok"
}
