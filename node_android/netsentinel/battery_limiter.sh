#!/system/bin/sh
# NetSentinel — Gerenciador de Saúde da Bateria 24/7 (Qualcomm PMI632 / qpnp-smb5)
# Validado empiricamente na Fase 0 (Moto E7 Plus, LineageOS 24.0):
#   - /sys/class/power_supply/battery/charging_enabled = 0 -> Modo Bypass Real (0 uA na bateria!)
#   - /sys/class/power_supply/battery/input_suspend = 1    -> Descarga suave controlada se > 82%
#   - /sys/class/power_supply/battery/temp                 -> Décimos de °C (400 = 40.0 °C)
#   - /sys/class/power_supply/usb/present                  -> 1=fonte externa presente, 0=queda de luz

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -f "${SCRIPT_DIR}/lib_common.sh" ] && [ -f "${NS_ROOT:-/data/adb/netsentinel}/lib_common.sh" ]; then
    SCRIPT_DIR="${NS_ROOT:-/data/adb/netsentinel}"
fi
# shellcheck source=lib_common.sh
. "${SCRIPT_DIR}/lib_common.sh"

# Retorna 1 se alimentação externa (tomada/USB) estiver fisicamente presente, 0 caso contrário.
# Totalmente desacoplado de charging_enabled=0 / input_suspend=1 (validado empiricamente).
is_external_power_present() {
    usb_present=$(read_sysfs_int "${POWER_SUPPLY_DIR}/usb/present" 0)
    usb_online=$(read_sysfs_int "${POWER_SUPPLY_DIR}/usb/online" 0)
    pc_online=$(read_sysfs_int "${POWER_SUPPLY_DIR}/pc_port/online" 0)
    usb_uv=$(read_sysfs_int "${POWER_SUPPLY_DIR}/usb/voltage_now" 0)

    if [ "$usb_present" -eq 1 ]; then
        echo "1"
        return 0
    fi
    if [ "$usb_online" -eq 1 ] || [ "$pc_online" -eq 1 ]; then
        echo "1"
        return 0
    fi
    if [ "$usb_uv" -ge 3500000 ]; then
        echo "1"
        return 0
    fi
    echo "0"
    return 0
}

# Coleta telemetria instantânea da bateria (exporta variáveis BATT_*)
read_battery_metrics() {
    BATT_PCT=$(read_sysfs_int "${POWER_SUPPLY_DIR}/battery/capacity" -1)
    BATT_TEMP_TENTHS=$(read_sysfs_int "${POWER_SUPPLY_DIR}/battery/temp" 0)
    BATT_VOLTAGE_UV=$(read_sysfs_int "${POWER_SUPPLY_DIR}/battery/voltage_now" 0)
    BATT_CURRENT_UA=$(read_sysfs_int "${POWER_SUPPLY_DIR}/battery/current_now" 0)
    BATT_STATUS=$(read_sysfs_str "${POWER_SUPPLY_DIR}/battery/status" "Unknown")
    BATT_CHARGING_ENABLED=$(read_sysfs_int "${POWER_SUPPLY_DIR}/battery/charging_enabled" 1)
    BATT_INPUT_SUSPEND=$(read_sysfs_int "${POWER_SUPPLY_DIR}/battery/input_suspend" 0)
    BATT_AC_PRESENT=$(is_external_power_present)

    # Converte décimos de °C para decimal com 1 casa (ex: 335 -> 33.5)
    BATT_TEMP_C=$(awk -v t="$BATT_TEMP_TENTHS" 'BEGIN { printf "%.1f", t / 10.0 }')
    BATT_VOLTAGE_MV=$((BATT_VOLTAGE_UV / 1000))
}

# Aplica política 60%–75% + corte térmico de emergência >= 40.0 °C
# Retorna via stdout a ação tomada: THERMAL_CUT | BYPASS_HOLD | GENTLE_DISCHARGE | CHARGE_ON | HYSTERESIS_KEEP | ON_BATTERY
apply_battery_policy() {
    ns_load_env
    ns_ensure_dirs
    read_battery_metrics

    state_file="${NS_STATE_DIR}/battery_limiter.state"
    thermal_lock=0
    if [ -r "$state_file" ]; then
        # shellcheck disable=SC1090
        . "$state_file"
    fi

    chg_node="${POWER_SUPPLY_DIR}/battery/charging_enabled"
    susp_node="${POWER_SUPPLY_DIR}/battery/input_suspend"

    action="ON_BATTERY"

    # 1. Proteção térmica absoluta (mesmo em qualquer faixa de porcentagem)
    if [ "$BATT_TEMP_TENTHS" -ge "$BATTERY_TEMP_EMERGENCY_TENTHS" ]; then
        write_sysfs_if_changed "$chg_node" "0" || true
        write_sysfs_if_changed "$susp_node" "0" || true
        thermal_lock=1
        action="THERMAL_CUT"
        ns_log "WARN" "battery_limiter: CORTE TERMICO DE EMERGENCIA (${BATT_TEMP_C}C >= 40.0C) — charging_enabled=0"
    elif [ "$thermal_lock" -eq 1 ] && [ "$BATT_TEMP_TENTHS" -gt "$BATTERY_TEMP_RESUME_TENTHS" ]; then
        # Histerese térmica: só libera recarga ao baixar para <= 37.0 °C
        write_sysfs_if_changed "$chg_node" "0" || true
        write_sysfs_if_changed "$susp_node" "0" || true
        action="THERMAL_CUT"
    else
        thermal_lock=0

        if [ "$BATT_AC_PRESENT" -eq 0 ]; then
            # Sem energia na tomada: restaura padrões para recarregar assim que a luz voltar se <= LOW
            write_sysfs_if_changed "$susp_node" "0" || true
            if [ "$BATT_PCT" -ge 0 ] && [ "$BATT_PCT" -le "$BATTERY_LIMIT_LOW" ]; then
                write_sysfs_if_changed "$chg_node" "1" || true
            fi
            action="ON_BATTERY"
        else
            # Com energia na tomada:
            if [ "$BATT_PCT" -lt 0 ] || [ "$BATT_PCT" -gt 100 ]; then
                # Leitura inválida momentânea da bateria: mantém em bypass preventivo
                write_sysfs_if_changed "$susp_node" "0" || true
                action="HYSTERESIS_KEEP"
            elif [ "$BATT_PCT" -ge "$BATTERY_FORCE_DISCHARGE_ABOVE" ]; then
                # Acima de 82% (ex.: recém-plugado em 100%): suspende entrada suavemente para descer até a janela segura
                write_sysfs_if_changed "$chg_node" "0" || true
                write_sysfs_if_changed "$susp_node" "1" || true
                action="GENTLE_DISCHARGE"
            elif [ "$BATT_PCT" -ge "$BATTERY_LIMIT_HIGH" ]; then
                # Faixa ideal >= 75%: Modo Bypass Real (0 uA na bateria, 0 micro-ciclos!)
                write_sysfs_if_changed "$susp_node" "0" || true
                write_sysfs_if_changed "$chg_node" "0" || true
                action="BYPASS_HOLD"
            elif [ "$BATT_PCT" -le "$BATTERY_LIMIT_LOW" ]; then
                # Atingiu piso de 60%: reativa carregamento até 75%
                write_sysfs_if_changed "$susp_node" "0" || true
                write_sysfs_if_changed "$chg_node" "1" || true
                action="CHARGE_ON"
            else
                # Banda de histerese (61%–74%): garante input_suspend=0 e preserva estado atual de charging_enabled
                write_sysfs_if_changed "$susp_node" "0" || true
                action="HYSTERESIS_KEEP"
            fi
        fi
    fi

    atomic_write "$state_file" "thermal_lock=${thermal_lock}
last_action=${action}
last_pct=${BATT_PCT}
last_temp_tenths=${BATT_TEMP_TENTHS}
last_ts=$(ns_now_epoch)
"

    echo "$action"
}

# Execução direta via CLI (ex.: ./battery_limiter.sh --apply ou --status)
if [ "${0##*/}" = "battery_limiter.sh" ]; then
    case "${1:-}" in
        --status)
            ns_load_env
            read_battery_metrics
            printf 'ac_present=%s pct=%s temp_c=%s voltage_mv=%s current_ua=%s status=%s charging_enabled=%s input_suspend=%s\n' \
                "$BATT_AC_PRESENT" "$BATT_PCT" "$BATT_TEMP_C" "$BATT_VOLTAGE_MV" "$BATT_CURRENT_UA" "$BATT_STATUS" "$BATT_CHARGING_ENABLED" "$BATT_INPUT_SUSPEND"
            ;;
        --apply|"")
            apply_battery_policy
            ;;
        *)
            echo "Uso: $0 [--apply|--status]" >&2
            exit 2
            ;;
    esac
fi
