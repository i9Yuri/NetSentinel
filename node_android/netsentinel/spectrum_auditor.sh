#!/system/bin/sh
# NetSentinel — Radar Passivo de Espectro (Wi-Fi + LAN + Bluetooth Cache)
# Validado empiricamente na Fase 0 (LineageOS 24.0 + Magisk v31.1):
#   1. Wi-Fi: `cmd wifi start-scan` + `cmd wifi list-scan-results` (fallback `iw wlan0 scan dump`)
#   2. LAN:   `ip neigh show` (detecta dispositivos desconhecidos conectados ao roteador)
#   3. BT:    `dumpsys bluetooth_manager` (extrai MACs/RSSI em cache se houver atividade)

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -f "${SCRIPT_DIR}/lib_common.sh" ] && [ -f "${NS_ROOT:-/data/adb/netsentinel}/lib_common.sh" ]; then
    SCRIPT_DIR="${NS_ROOT:-/data/adb/netsentinel}"
fi
# shellcheck source=lib_common.sh
. "${SCRIPT_DIR}/lib_common.sh"

WHITELIST_FILE="${WHITELIST_FILE:-${NS_ROOT}/whitelist.conf}"
BLACKLIST_FILE="${BLACKLIST_FILE:-${NS_ROOT}/blacklist.conf}"
TRACKER_FILE="${TRACKER_FILE:-${NS_STATE_DIR}/spectrum_tracker.tsv}"
ANOMALIES_PENDING_FILE="${ANOMALIES_PENDING_FILE:-${NS_STATE_DIR}/spectrum_anomalies.jsonl}"
BLACKBOX_LOG="${BLACKBOX_LOG:-${NS_ROOT}/blackbox.log}"

# Comandos sobreponíveis via variável de ambiente para testes unitários determinísticos
WIFI_SCAN_TRIGGER_CMD="${WIFI_SCAN_TRIGGER_CMD:-cmd wifi start-scan}"
WIFI_SCAN_LIST_CMD="${WIFI_SCAN_LIST_CMD:-cmd wifi list-scan-results}"
NEIGH_CMD="${NEIGH_CMD:-ip neigh show}"
BT_DUMP_CMD="${BT_DUMP_CMD:-dumpsys bluetooth_manager}"

# Normaliza whitelist em conjunto consultável
is_whitelisted() {
    mac_l=$(printf '%s' "$1" | tr 'A-F' 'a-f')
    [ -r "$WHITELIST_FILE" ] || return 1
    grep -iEq "^[[:space:]]*${mac_l}([[:space:]]|#|$)" "$WHITELIST_FILE"
}

# Normaliza blacklist em conjunto consultável
is_blacklisted() {
    mac_l=$(printf '%s' "$1" | tr 'A-F' 'a-f')
    [ -r "$BLACKLIST_FILE" ] || return 1
    grep -iEq "^[[:space:]]*${mac_l}([[:space:]]|#|$)" "$BLACKLIST_FILE"
}

# Coleta observações normalizadas no formato TSV:
#   source<TAB>mac_lower<TAB>rssi_int<TAB>label
collect_spectrum_observations() {
    # 1. Wi-Fi (opcional: desativado por padrão para evitar falsos alarmes com repetidores e vizinhos)
    if [ "${SPECTRUM_SCAN_WIFI_APS:-0}" -eq 1 ]; then
        if [ "${SPECTRUM_SKIP_TRIGGER:-0}" -eq 0 ]; then
            sh -c "$WIFI_SCAN_TRIGGER_CMD" >/dev/null 2>&1 || true
            sleep "${SPECTRUM_SCAN_WAIT_SEC:-2}"
        fi

        wifi_raw=$(sh -c "$WIFI_SCAN_LIST_CMD" 2>/dev/null || true)
        if [ -n "$wifi_raw" ]; then
            # Formato real validado no Android 17 / LineageOS 24.0:
            #   ec:b9:31:fc:71:b4  2457  -34(0:-34)  1.080  Isreali Intelligence Agency Cam  [WPA2-...]
            printf '%s\n' "$wifi_raw" | awk '
            $1 ~ /^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$/ {
                mac = tolower($1)
                rssi = $3
                sub(/\(.*/, "", rssi)
                if (rssi !~ /^-?[0-9]+$/) next
                # Extrai SSID entre coluna 4 (Age) e o primeiro "[" de Flags
                line = $0
                sub(/^[[:space:]]*[^[:space:]]+[[:space:]]+[^[:space:]]+[[:space:]]+[^[:space:]]+[[:space:]]+[^[:space:]]+[[:space:]]*/, "", line)
                sub(/[[:space:]]*\[.*$/, "", line)
                gsub(/[[:space:]]+$/, "", line)
                if (line == "") line = "<hidden>"
                printf "wifi\t%s\t%d\t%s\n", mac, rssi, line
            }'
        elif command -v iw >/dev/null 2>&1; then
            iw wlan0 scan dump 2>/dev/null | awk '
            /^BSS / {
                if (mac != "" && rssi != "") printf "wifi\t%s\t%d\t%s\n", mac, rssi, (ssid != "" ? ssid : "<hidden>")
                mac = tolower($2); sub(/\(.*/, "", mac); rssi = ""; ssid = ""
            }
            /signal:/ { rssi = int($2) }
            /SSID:/ { sub(/^[[:space:]]*SSID:[[:space:]]*/, ""); ssid = $0 }
            END { if (mac != "" && rssi != "") printf "wifi\t%s\t%d\t%s\n", mac, rssi, (ssid != "" ? ssid : "<hidden>") }
            '
        fi
    fi

    # 2. LAN ARP/NDP (dispositivos ativos na mesma rede local do apartamento)
    neigh_raw=$(sh -c "$NEIGH_CMD" 2>/dev/null || true)
    if [ -n "$neigh_raw" ]; then
        printf '%s\n' "$neigh_raw" | awk '
        /lladdr/ && ($NF == "REACHABLE" || $NF == "DELAY" || $NF == "PROBE" || $NF == "STALE") {
            ip = $1; mac = "";
            for (i = 1; i <= NF; i++) {
                if ($i == "lladdr" && (i + 1) <= NF) { mac = tolower($(i + 1)); break }
            }
            if (mac ~ /^([0-9a-f]{2}:){5}[0-9a-f]{2}$/) {
                # Dispositivo na LAN interna recebe RSSI sintético -40 dBm (alta proximidade lógica)
                printf "lan\t%s\t-40\tLAN:%s\n", mac, ip
            }
        }'
    fi

    # 3. Bluetooth / BLE cache do Android framework (quando presente em dumpsys)
    bt_raw=$(sh -c "$BT_DUMP_CMD" 2>/dev/null || true)
    if [ -n "$bt_raw" ]; then
        printf '%s\n' "$bt_raw" | awk '
        tolower($0) ~ /rssi/ && match($0, /([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}/) {
            mac = tolower(substr($0, RSTART, RLENGTH))
            if (mac == "00:00:00:00:00:00") next
            rest = $0
            if (match(rest, /rssi[=: ]+(-?[0-9]+)/)) {
                r = substr(rest, RSTART, RLENGTH)
                sub(/^[^0-9-]+/, "", r)
                printf "ble\t%s\t%d\tBLE\n", mac, int(r)
            }
        }'
    fi
}

# Coleta lista completa e categorizada de dispositivos da LAN em formato JSON
collect_lan_devices_json() {
    my_ip=$(ip -4 addr show dev wlan0 2>/dev/null | awk '/inet / {sub(/\/.*/, "", $2); print $2; exit}')
    bcast=$(ip -4 addr show dev wlan0 2>/dev/null | awk '/inet / {print $4; exit}')
    [ -z "$bcast" ] && bcast="192.168.0.255"

    # Varredura paralela rápida na sub-rede (/24) para forçar resposta de todos os hosts (cabo, wi-fi, tvs, alexas)
    subnet_prefix=$(printf '%s' "$my_ip" | cut -d. -f1-3)
    if [ -n "$subnet_prefix" ] && [ "${SPECTRUM_SKIP_PING_SWEEP:-0}" -eq 0 ]; then
        for i in $(seq 1 254); do
            ping -c 1 -W 1 "${subnet_prefix}.${i}" >/dev/null 2>&1 &
        done
        wait
    else
        ping -c 1 -b -W 1 "$bcast" >/dev/null 2>&1 || true
    fi

    tmp_lan="${NS_STATE_DIR}/lan_scan.$$.tsv"
    : > "$tmp_lan"

    # Adiciona o próprio nó
    if [ -n "$my_ip" ]; then
        my_mac=$(cat /sys/class/net/wlan0/address 2>/dev/null | tr 'A-F' 'a-f')
        [ -z "$my_mac" ] && my_mac="00:00:00:00:00:00"
        printf '%s\t%s\tMotorola\tVIGIA\tCelular Vigia (Moto E7 Plus)\t1\n' "$my_ip" "$my_mac" >> "$tmp_lan"
    fi

    # Varre /proc/net/arp e ip neigh show
    {
        if [ -r /proc/net/arp ]; then
            awk 'NR > 1 && $4 ~ /^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$/ && $4 != "00:00:00:00:00:00" {
                printf "%s\t%s\n", $1, tolower($4)
            }' /proc/net/arp
        fi
        ip neigh show dev wlan0 2>/dev/null | awk '
        /lladdr/ && $1 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ {
            for (i=1; i<=NF; i++) {
                if ($i == "lladdr" && (i+1) <= NF) {
                    printf "%s\t%s\n", $1, tolower($(i+1))
                    break
                }
            }
        }'
    } | sort -u -k1,1 | while IFS='	' read -r ip mac; do
        [ -z "$ip" ] || [ -z "$mac" ] && continue
        [ "$ip" = "$my_ip" ] && continue

        vendor=$(oui_lookup "$mac")
        cls_info=$(classify_device "$mac" "$ip" "$vendor" "" "192.168.0.1" "$my_ip")
        cat_key=$(printf '%s' "$cls_info" | awk -F'\t' '{print $1}')
        cat_name=$(printf '%s' "$cls_info" | awk -F'\t' '{print $3}')

        known=0
        if is_whitelisted "$mac"; then
            known=1
        fi
        if is_blacklisted "$mac"; then
            known=0
        fi

        printf '%s\t%s\t%s\t%s\t%s\t%d\n' "$ip" "$mac" "$vendor" "$cat_key" "$cat_name" "$known" >> "$tmp_lan"
    done

    # Emite JSON array
    printf '['
    first=1
    while IFS='	' read -r ip mac vendor category name known; do
        [ -z "$ip" ] && continue
        [ "$first" -eq 0 ] && printf ','
        first=0
        is_known_bool="true"
        [ "$known" -eq 0 ] && is_known_bool="false"
        esc_vendor=$(json_escape "$vendor")
        esc_cat=$(json_escape "$category")
        esc_name=$(json_escape "$name")
        printf '{"ip":"%s","mac":"%s","vendor":"%s","category":"%s","name":"%s","is_known":%s}' \
            "$ip" "$mac" "$esc_vendor" "$esc_cat" "$esc_name" "$is_known_bool"
    done < "$tmp_lan"
    printf ']'
    rm -f "$tmp_lan"
}

# Modo aprendizado: fotografa todos os vizinhos/roteadores e dispositivos LAN na whitelist
learn_whitelist() {
    ns_load_env
    ns_ensure_dirs
    obs=$(collect_spectrum_observations)
    count=0
    [ -f "$WHITELIST_FILE" ] || printf '# NetSentinel Whitelist\n' > "$WHITELIST_FILE"
    printf '%s\n' "$obs" | while IFS='	' read -r src mac rssi label; do
        [ -z "$mac" ] && continue
        if ! is_whitelisted "$mac"; then
            printf '%s # %s rssi=%s %s\n' "$mac" "$src" "$rssi" "$label" >> "$WHITELIST_FILE"
            count=$((count + 1))
        fi
    done

    # Também fotografa e confia em todos os dispositivos atualmente conectados na LAN
    if [ -r /proc/net/arp ]; then
        awk 'NR > 1 && $4 ~ /^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$/ && $4 != "00:00:00:00:00:00" {
            printf "%s\t%s\n", tolower($4), $1
        }' /proc/net/arp | while IFS='	' read -r lmac lip; do
            [ -z "$lmac" ] && continue
            if ! is_whitelisted "$lmac"; then
                lvendor=$(oui_lookup "$lmac")
                printf '%s # lan IP:%s (%s)\n' "$lmac" "$lip" "$lvendor" >> "$WHITELIST_FILE"
                count=$((count + 1))
            fi
        done
    fi

    if command -v fsync >/dev/null 2>&1; then
        fsync "$WHITELIST_FILE" >/dev/null 2>&1 || sync
    fi
    ns_log "INFO" "spectrum_auditor: whitelist atualizada em $WHITELIST_FILE"
    echo "WHITELIST_UPDATED"
}

# Executa 1 ciclo completo de auditoria de espectro.
# Args: [now_ts]
# Formato do tracker TSV: mac<TAB>source<TAB>first_seen_ts<TAB>last_seen_ts<TAB>max_rssi<TAB>last_alert_ts<TAB>label
run_spectrum_audit() {
    ns_load_env
    ns_ensure_dirs
    now_ts="${1:-$(ns_now_epoch)}"

    obs_file="${NS_STATE_DIR}/spectrum_obs.$$.tsv"
    new_tracker="${TRACKER_FILE}.next.$$"
    : > "$new_tracker"

    # Deduplica observações mantendo o maior RSSI por MAC nesta rodada
    collect_spectrum_observations | awk -F'\t' '
    NF >= 3 {
        mac = tolower($2); rssi = int($3); src = $1; lbl = $4
        if (!(mac in seen) || rssi > best_rssi[mac]) {
            seen[mac] = 1
            best_rssi[mac] = rssi
            best_src[mac] = src
            best_lbl[mac] = lbl
        }
    }
    END {
        for (m in seen) {
            printf "%s\t%s\t%d\t%s\n", best_src[m], m, best_rssi[m], best_lbl[m]
        }
    }' > "$obs_file"

    # Carrega estado anterior e calcula anomalias em um único passo atômico
    anomalies_found=0
    while IFS='	' read -r src mac rssi label; do
        [ -z "$mac" ] && continue
        # Ignora dispositivos confiáveis
        if is_whitelisted "$mac"; then
            continue
        fi
        # Só rastreia sinais fortes (proximidade física imediata) ou dispositivos na LAN
        if [ "$src" != "lan" ] && [ "$rssi" -lt "$SPECTRUM_RSSI_THRESHOLD" ]; then
            continue
        fi

        # Busca registro anterior deste MAC no tracker
        prev_line=""
        if [ -r "$TRACKER_FILE" ]; then
            prev_line=$(awk -F'\t' -v m="$mac" '$1 == m { print; exit }' "$TRACKER_FILE")
        fi

        first_seen="$now_ts"
        max_rssi="$rssi"
        last_alert_ts=0

        if [ -n "$prev_line" ]; then
            p_first=$(printf '%s' "$prev_line" | awk -F'\t' '{print $3}')
            p_last=$(printf '%s' "$prev_line" | awk -F'\t' '{print $4}')
            p_max=$(printf '%s' "$prev_line" | awk -F'\t' '{print $5}')
            p_alert=$(printf '%s' "$prev_line" | awk -F'\t' '{print $6}')

            # Se reapareceu dentro da janela de continuidade, mantém first_seen
            # Para LAN, mantém janela de 24h (86400s) para evitar falsos alarmes com celulares em repouso
            # Para Wi-Fi/BLE externo, mantém janela de 300s
            gap=$((now_ts - p_last))
            continuity_window=300
            [ "$src" = "lan" ] && continuity_window=86400
            if [ "$gap" -le "$continuity_window" ]; then
                first_seen="$p_first"
                if [ "$p_max" -gt "$max_rssi" ]; then
                    max_rssi="$p_max"
                fi
            fi
            last_alert_ts="${p_alert:-0}"
        fi

        persist_sec=$((now_ts - first_seen))
        cooldown_elapsed=$((now_ts - last_alert_ts))

        # Dispara anomalia:
        # - Para LAN: apenas se NUNCA foi alertado (last_alert_ts == 0); uma vez alertado, é conhecido e não spamma
        # - Para Wi-Fi/BLE externo: se persistiu >= SPECTRUM_PERSIST_MIN_SEC e respeitou cooldown
        trigger=0
        if [ "$src" = "lan" ]; then
            if [ "$last_alert_ts" -eq 0 ]; then
                trigger=1
            fi
        elif [ "$persist_sec" -ge "$SPECTRUM_PERSIST_MIN_SEC" ] && [ "$cooldown_elapsed" -ge "$SPECTRUM_ALERT_COOLDOWN_SEC" ]; then
            trigger=1
        fi

        if [ "$trigger" -eq 1 ]; then
            vendor=$(oui_lookup "$mac")
            esc_lbl=$(json_escape "$label")
            esc_vendor=$(json_escape "$vendor")
            ev="{\"event\":\"SPECTRUM_ANOMALY\",\"ts\":${now_ts},\"source\":\"${src}\",\"mac\":\"${mac}\",\"vendor\":\"${esc_vendor}\",\"rssi_max\":${max_rssi},\"rssi_now\":${rssi},\"persist_sec\":${persist_sec},\"label\":\"${esc_lbl}\"}"
            atomic_append_line "$ANOMALIES_PENDING_FILE" "$ev"
            atomic_append_line "$BLACKBOX_LOG" "$ev"
            ns_log "WARN" "spectrum_auditor: ANOMALIA PROXIMIDADE mac=${mac} (${vendor}) src=${src} rssi=${max_rssi}dBm persist=${persist_sec}s"
            last_alert_ts="$now_ts"
            anomalies_found=$((anomalies_found + 1))
        fi

        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$mac" "$src" "$first_seen" "$now_ts" "$max_rssi" "$last_alert_ts" "$label" >> "$new_tracker"
    done < "$obs_file"

    # Preserva entradas recentes do tracker que falharam em apenas 1 ciclo (evita jitter de beacon)
    if [ -r "$TRACKER_FILE" ]; then
        while IFS='	' read -r omac osrc ofirst olast omax oalert olabel; do
            [ -z "$omac" ] && continue
            age=$((now_ts - olast))
            retention_window=300
            [ "$osrc" = "lan" ] && retention_window=86400
            if [ "$age" -le "$retention_window" ]; then
                if ! awk -F'\t' -v m="$omac" '$1 == m { found=1; exit } END { exit !found }' "$new_tracker" 2>/dev/null; then
                    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
                        "$omac" "$osrc" "$ofirst" "$olast" "$omax" "$oalert" "$olabel" >> "$new_tracker"
                fi
            fi
        done < "$TRACKER_FILE"
    fi

    rm -f "$obs_file"
    if command -v fsync >/dev/null 2>&1; then
        fsync "$new_tracker" >/dev/null 2>&1 || sync
    fi
    mv -f "$new_tracker" "$TRACKER_FILE"
    echo "$anomalies_found"
}

get_pending_anomalies_json_array() {
    if [ ! -s "$ANOMALIES_PENDING_FILE" ]; then
        echo "[]"
        return 0
    fi
    awk 'BEGIN { printf "[" }
         NR > 1 { printf "," }
         { printf "%s", $0 }
         END { printf "]" }' "$ANOMALIES_PENDING_FILE"
}

clear_pending_anomalies() {
    atomic_write "$ANOMALIES_PENDING_FILE" ""
}

if [ "${0##*/}" = "spectrum_auditor.sh" ]; then
    case "${1:-}" in
        --learn-whitelist)
            learn_whitelist
            ;;
        --scan|"")
            shift 2>/dev/null || true
            run_spectrum_audit "${1:-}"
            ;;
        --pending)
            get_pending_anomalies_json_array
            echo ""
            ;;
        --lan-json)
            collect_lan_devices_json
            echo ""
            ;;
        --clear-pending)
            clear_pending_anomalies
            ;;
        *)
            echo "Uso: $0 [--scan [ts] | --learn-whitelist | --pending | --lan-json | --clear-pending]" >&2
            exit 2
            ;;
    esac
fi
