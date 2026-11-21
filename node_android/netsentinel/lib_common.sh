#!/system/bin/sh
# NetSentinel — Biblioteca POSIX compartilhada (Android 17 / LineageOS 24.0 + Linux)
# Garante: escrita atômica (tmp + fsync + mv), HMAC-SHA256 RFC 2104 puro,
# escape JSON seguro, gerenciamento de wakelocks e logging rotacionado.

# Diretórios configuráveis (permite testes isolados em host Linux)
NS_ROOT="${NS_ROOT:-/data/adb/netsentinel}"
NS_STATE_DIR="${NS_STATE_DIR:-${NS_ROOT}/state}"
NS_LOG_DIR="${NS_LOG_DIR:-${NS_ROOT}/logs}"
POWER_SUPPLY_DIR="${POWER_SUPPLY_DIR:-/sys/class/power_supply}"
WAKELOCK_DIR="${WAKELOCK_DIR:-/sys/power}"
WAKELOCK_NAME="${WAKELOCK_NAME:-netsentinel_cycle_wl}"
OUI_TABLE_FILE="${OUI_TABLE_FILE:-${NS_ROOT}/oui_table.tsv}"

# Garante que busybox/toybox/system bins estejam no PATH
export PATH="/product/bin:/system/bin:/system_ext/bin:/vendor/bin:/data/adb/magisk:${PATH}"

ns_now_epoch() {
    date -u +%s
}

ns_now_iso() {
    date -u +"%Y-%m-%dT%H:%M:%SZ"
}

ns_uptime_sec() {
    if [ -r /proc/uptime ]; then
        awk '{printf "%d", $1}' /proc/uptime
    else
        echo "0"
    fi
}

ns_ensure_dirs() {
    mkdir -p "$NS_STATE_DIR" "$NS_LOG_DIR"
}

ns_log() {
    level="$1"
    shift
    msg="$*"
    ts="$(ns_now_iso)"
    printf '%s [%s] %s\n' "$ts" "$level" "$msg" >&2
    if [ -d "$NS_LOG_DIR" ]; then
        logfile="${NS_LOG_DIR}/node.log"
        printf '%s [%s] %s\n' "$ts" "$level" "$msg" >> "$logfile" 2>/dev/null || true
        # Rotação simples acima de 512 KB para proteger eMMC
        if [ -f "$logfile" ]; then
            sz=$(wc -c < "$logfile" 2>/dev/null || echo 0)
            if [ "${sz:-0}" -gt 524288 ]; then
                mv -f "$logfile" "${logfile}.1" 2>/dev/null || true
            fi
        fi
    fi

    # Rotação preventiva do daemon.log para proteger memória flash
    daemon_log="${NS_ROOT}/daemon.log"
    if [ -f "$daemon_log" ]; then
        dsz=$(wc -c < "$daemon_log" 2>/dev/null || echo 0)
        if [ "${dsz:-0}" -gt 262144 ]; then
            mv -f "$daemon_log" "${daemon_log}.1" 2>/dev/null || true
        fi
    fi
}

# Escrita atômica: arquivo temporário no mesmo filesystem + fsync + rename (mv)
atomic_write() {
    target="$1"
    content="$2"
    dir=$(dirname "$target")
    mkdir -p "$dir"
    tmp="${target}.tmp.$$"
    printf '%s' "$content" > "$tmp" || return 1
    if command -v fsync >/dev/null 2>&1; then
        fsync "$tmp" >/dev/null 2>&1 || sync
    else
        sync
    fi
    mv -f "$tmp" "$target" || { rm -f "$tmp"; return 1; }
    if command -v fsync >/dev/null 2>&1; then
        fsync "$dir" >/dev/null 2>&1 || true
    fi
    return 0
}

# Append durável para caixa-preta (escreve linha + fsync imediato)
atomic_append_line() {
    target="$1"
    line="$2"
    dir=$(dirname "$target")
    mkdir -p "$dir"
    printf '%s\n' "$line" >> "$target" || return 1
    if command -v fsync >/dev/null 2>&1; then
        fsync "$target" >/dev/null 2>&1 || sync
    else
        sync
    fi
    return 0
}

# Escape JSON seguro (sem depender de jq ou python)
json_escape() {
    printf '%s' "$1" | awk '
    BEGIN { ORS="" }
    {
        if (NR > 1) printf "\\n"
        gsub(/\\/, "\\\\")
        gsub(/"/, "\\\"")
        gsub(/\r/, "\\r")
        gsub(/\t/, "\\t")
        printf "%s", $0
    }'
}

# Leitura segura de inteiro em arquivo sysfs (com fallback padrão)
read_sysfs_int() {
    path="$1"
    default_val="${2:-0}"
    if [ -r "$path" ]; then
        val=$(head -n 1 "$path" 2>/dev/null | tr -d ' \r\n')
        case "$val" in
            ''|*[!0-9-]*) echo "$default_val" ;;
            *) echo "$val" ;;
        esac
    else
        echo "$default_val"
    fi
}

read_sysfs_str() {
    path="$1"
    default_val="${2:-}"
    if [ -r "$path" ]; then
        head -n 1 "$path" 2>/dev/null | tr -d '\r\n'
    else
        echo "$default_val"
    fi
}

# Escrita controlada em sysfs apenas se o valor mudar (evita desgaste/ruído)
write_sysfs_if_changed() {
    path="$1"
    desired="$2"
    [ -w "$path" ] || return 1
    current=$(head -n 1 "$path" 2>/dev/null | tr -d ' \r\n')
    if [ "$current" != "$desired" ]; then
        printf '%s' "$desired" > "$path" 2>/dev/null || return 1
    fi
    return 0
}

# Gerenciamento de partial wakelock (/sys/power/wake_lock e wake_unlock)
wakelock_acquire() {
    if [ -w "${WAKELOCK_DIR}/wake_lock" ]; then
        printf '%s' "$WAKELOCK_NAME" > "${WAKELOCK_DIR}/wake_lock" 2>/dev/null || true
    fi
}

wakelock_release() {
    if [ -w "${WAKELOCK_DIR}/wake_unlock" ]; then
        printf '%s' "$WAKELOCK_NAME" > "${WAKELOCK_DIR}/wake_unlock" 2>/dev/null || true
    fi
}

# Proteção contra Simple LMK / lmkd
protect_from_lmk() {
    if [ -w "/proc/$$/oom_score_adj" ]; then
        printf '%s' "-1000" > "/proc/$$/oom_score_adj" 2>/dev/null || true
    fi
}

# HMAC-SHA256 RFC 2104 em POSIX puro (usa openssl se existir; senão sha256sum + xxd + awk)
# Assinatura: hmac_sha256_hex <secret> <message>
hmac_sha256_hex() {
    secret="$1"
    message="$2"

    # Caminho rápido quando openssl está presente (ex.: host Linux)
    if command -v openssl >/dev/null 2>&1; then
        printf '%s' "$message" | openssl dgst -sha256 -hmac "$secret" -r 2>/dev/null | awk '{print tolower($1)}'
        return 0
    fi

    # Caminho POSIX portátil para Android (sha256sum + xxd + awk — validado no Moto E7 Plus)
    key_len=${#secret}
    if [ "$key_len" -gt 64 ]; then
        key_hex=$(printf '%s' "$secret" | sha256sum | awk '{print $1}')
    else
        key_hex=$(printf '%s' "$secret" | xxd -p | tr -d '\r\n ')
    fi

    # Gera ipad (key XOR 0x36) e opad (key XOR 0x5c) de 64 bytes em awk POSIX puro
    pads=$(awk -v kh="$key_hex" '
    function hexval(c,   idx) {
        idx = index("0123456789abcdef", tolower(c))
        return (idx > 0) ? (idx - 1) : 0
    }
    function bxor(a, b,   r, bit) {
        r = 0; bit = 1
        while (a > 0 || b > 0) {
            if ((a % 2) != (b % 2)) r += bit
            a = int(a / 2); b = int(b / 2); bit *= 2
        }
        return r
    }
    BEGIN {
        while (length(kh) < 128) kh = kh "00"
        ipad = ""; opad = ""
        for (i = 1; i <= 128; i += 2) {
            byte = hexval(substr(kh, i, 1)) * 16 + hexval(substr(kh, i + 1, 1))
            ipad = ipad sprintf("%02x", bxor(byte, 54))
            opad = opad sprintf("%02x", bxor(byte, 92))
        }
        print ipad " " opad
    }')

    ipad_hex=$(printf '%s' "$pads" | awk '{print $1}')
    opad_hex=$(printf '%s' "$pads" | awk '{print $2}')

    inner_hex=$(
        {
            printf '%s' "$ipad_hex" | xxd -r -p
            printf '%s' "$message"
        } | sha256sum | awk '{print $1}'
    )

    {
        printf '%s' "$opad_hex" | xxd -r -p
        printf '%s' "$inner_hex" | xxd -r -p
    } | sha256sum | awk '{print tolower($1)}'
}

# Resolução offline de fabricante por OUI (primeiros 3 octetos do MAC)
oui_lookup() {
    mac_lower=$(printf '%s' "$1" | tr 'A-F' 'a-f')
    prefix=$(printf '%s' "$mac_lower" | cut -d: -f1-3)
    if [ -r "$OUI_TABLE_FILE" ]; then
        vendor=$(awk -F'\t' -v p="$prefix" '$1 == p { print $2; exit }' "$OUI_TABLE_FILE" 2>/dev/null)
        if [ -n "$vendor" ]; then
            printf '%s' "$vendor"
            return 0
        fi
    fi
    # Detecta MAC localmente administrado (bit 1 do primeiro octeto = 1 -> randomização mobile)
    second_nibble=$(printf '%s' "$prefix" | cut -c2)
    case "$second_nibble" in
        2|6|a|e) printf 'Randomized/Mobile' ;;
        *) printf 'Unknown' ;;
    esac
}

# Classificação inteligente de dispositivos de rede
# Retorna: CATEGORIA<TAB>ICONE<TAB>NOME_AMIGAVEL
classify_device() {
    mac_lower=$(printf '%s' "$1" | tr 'A-F' 'a-f')
    ip="$2"
    vendor="$3"
    label="$4"
    gw_ip="${5:-192.168.0.1}"
    my_ip="${6:-}"

    # 1. Celular Vigia (próprio nó)
    if [ -n "$my_ip" ] && [ "$ip" = "$my_ip" ]; then
        printf 'VIGIA\t🛡️\tCelular Vigia (Moto E7 Plus)'
        return 0
    fi

    # 2. Roteador ou Repetidor
    case "$ip" in
        "$gw_ip"|*.1|*.254)
            printf 'ROUTER\t🌐\tRoteador Principal (%s)' "$vendor"
            return 0
            ;;
    esac
    case "$vendor" in
        Intelbras*|*ZTE*|*Huawei*|*TP-Link*|*D-Link*|*Ubiquiti*|*Mikrotik*|*Tenda*|*FiberHome*|*Router*)
            printf 'ROUTER\t🌐\tRoteador / Repetidor (%s)' "$vendor"
            return 0
            ;;
    esac

    # 3. Smart TV / Streaming
    case "$vendor" in
        *LG*|*Roku*|*Google*Chromecast*|*GSD*)
            clean_v="Smart TV"
            case "$vendor" in
                *LG*) clean_v="LG" ;;
                *Roku*) clean_v="Roku" ;;
                *GSD*) clean_v="GSD" ;;
            esac
            printf 'SMART_TV\t📺\tSmart TV (%s)' "$clean_v"
            return 0
            ;;
    esac
    case "$label" in
        *[tT][vV]*|*[tT]izen*|*[wW]ebos*|*[bB]ravia*|*[cC]hromecast*)
            printf 'SMART_TV\t📺\tSmart TV / Streaming'
            return 0
            ;;
    esac

    # 4. Samsung
    if [ "$vendor" = "Samsung" ] || [ "$vendor" = "Samsung Electronics" ]; then
        case "$label" in
            *[tT][vV]*|*[dD]isplay*)
                printf 'SMART_TV\t📺\tSmart TV Samsung'
                return 0
                ;;
            *)
                printf 'SMARTPHONE\t📱\tSmartphone Samsung Galaxy'
                return 0
                ;;
        esac
    fi

    # 5. Smartphone / Tablet
    case "$vendor" in
        Apple*)
            printf 'SMARTPHONE\t📱\tiPhone / iPad'
            return 0
            ;;
        Motorola*|Xiaomi*|OnePlus*|Oppo*|Vivo*|Realme*)
            clean_m=$(printf '%s' "$vendor" | awk '{print $1}')
            printf 'SMARTPHONE\t📱\tSmartphone (%s)' "$clean_m"
            return 0
            ;;
    esac

    # 6. Casa Inteligente / IoT / Áudio
    case "$vendor" in
        Amazon*|*Echo*|*FireTV*)
            printf 'IOT\t💡\tAmazon Echo / Alexa'
            return 0
            ;;
        *Audio*|*Soundbar*|*Richsound*)
            printf 'IOT\t🔊\tCaixa de Som / Soundbar'
            return 0
            ;;
        Espressif*|Tuya*|Sonoff*|Broadlink*|Shelly*|*Philips*Hue*|Aqara*|*IoT*)
            clean_iot=$(printf '%s' "$vendor" | awk '{print $1}')
            printf 'IOT\t💡\tDispositivo IoT (%s)' "$clean_iot"
            return 0
            ;;
    esac

    # 7. Computadores / Notebooks
    case "$vendor" in
        *Dell*|*Lenovo*|*HP*|*Intel*|*Realtek*|*PC*|*Microsoft*|*Asus*|*Acer*|*MSI*|*Gigabyte*)
            printf 'COMPUTER\t💻\tComputador / Notebook'
            return 0
            ;;
    esac

    # 8. MAC Aleatório / Dispositivo Móvel moderno
    second_nibble=$(printf '%s' "$mac_lower" | cut -c2)
    case "$second_nibble" in
        2|6|a|e)
            printf 'MOBILE_RANDOM\t📱\tCelular / Tablet (Privacidade ativa)'
            return 0
            ;;
    esac

    # 9. Genérico
    printf 'UNKNOWN\t❓\tAparelho Conectado (%s)' "$vendor"
}

# Detecção inteligente e dinâmica de localização geográfica do aparelho
ns_detect_location() {
    cache_file="${NS_STATE_DIR}/detected_location.cache"
    now_ts=$(ns_now_epoch)

    # 1. Cache local com validade de 30 minutos (1800s)
    if [ -r "$cache_file" ]; then
        cached_ts=$(head -n 1 "$cache_file" 2>/dev/null | awk '{print $1}')
        cached_loc=$(tail -n +2 "$cache_file" 2>/dev/null)
        if [ -n "$cached_ts" ] && [ $((now_ts - cached_ts)) -lt 1800 ] && [ -n "$cached_loc" ]; then
            printf '%s' "$cached_loc"
            return 0
        fi
    fi

    # 2. SSID / BSSID da interface wlan0
    curr_ssid=$(dumpsys wifi 2>/dev/null | awk '/mWifiInfo / {if (match($0, /SSID: "([^"]+)"/)) {print substr($0, RSTART+7, RLENGTH-8); exit}}')
    curr_bssid=$(dumpsys wifi 2>/dev/null | awk '/mWifiInfo / {if (match($0, /BSSID: ([0-9a-fA-F:]{17})/)) {print tolower(substr($0, RSTART+7, 17)); exit}}')

    # Heurística direta pelo Wi-Fi de Campina Grande
    if [ "$curr_bssid" = "24:2f:d0:76:41:b4" ] || [ "$curr_ssid" = "brisa-1082152" ]; then
        loc="Casa Campina Grande (Quarto)"
        printf '%d\n%s' "$now_ts" "$loc" > "$cache_file" 2>/dev/null || true
        printf '%s' "$loc"
        return 0
    fi

    # 3. GeoIP rápido via ip-api.com (timeout 3s)
    geo=$(curl -s -m 3 http://ip-api.com/json 2>/dev/null || true)
    city=$(printf '%s' "$geo" | awk -F'"city":"' 'NF>1 {split($2,a,"\""); print a[1]}')

    if [ -n "$city" ]; then
        case "$city" in
            *Campina*Grande*)
                loc="Casa Campina Grande (Quarto)"
                ;;
            *Cabedelo*|*João*Pessoa*|*Joao*Pessoa*)
                loc="Apê João Pessoa (Ponta de Campina)"
                ;;
            *)
                loc="${city} - PB"
                [ -n "$curr_ssid" ] && loc="${city} (${curr_ssid})"
                ;;
        esac
        printf '%d\n%s' "$now_ts" "$loc" > "$cache_file" 2>/dev/null || true
        printf '%s' "$loc"
        return 0
    fi

    # 4. Fallback padrão se offline
    printf '%s' "${NETSENTINEL_LOCATION_LABEL:-Campina Grande}"
}

# Carrega variáveis do node.env (sem falhar se ausente em modo de teste)
ns_load_env() {
    env_file="${NS_ENV_FILE:-${NS_ROOT}/node.env}"
    if [ -r "$env_file" ]; then
        # shellcheck disable=SC1090
        . "$env_file"
    fi
    NETSENTINEL_NODE_ID="${NETSENTINEL_NODE_ID:-e7plus-jp-ap}"

    # Detecção inteligente de onde o aparelho está no momento (Campina Grande vs João Pessoa)
    auto_loc=$(ns_detect_location 2>/dev/null || true)
    if [ -n "$auto_loc" ]; then
        NETSENTINEL_LOCATION_LABEL="$auto_loc"
    else
        NETSENTINEL_LOCATION_LABEL="${NETSENTINEL_LOCATION_LABEL:-Campina Grande}"
    fi
    NETSENTINEL_HMAC_SECRET="${NETSENTINEL_HMAC_SECRET:-}"
    HUB_ENDPOINT_URL="${HUB_ENDPOINT_URL:-}"
    TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
    TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
    HEARTBEAT_INTERVAL_SEC="${HEARTBEAT_INTERVAL_SEC:-60}"
    SURVIVAL_POLL_INTERVAL_SEC="${SURVIVAL_POLL_INTERVAL_SEC:-120}"
    STATUS_CARD_INTERVAL_SEC="${STATUS_CARD_INTERVAL_SEC:-300}"
    SPECTRUM_SCAN_INTERVAL_SEC="${SPECTRUM_SCAN_INTERVAL_SEC:-60}"
    SPECTRUM_SCAN_WIFI_APS="${SPECTRUM_SCAN_WIFI_APS:-0}"
    LOCAL_TIMEZONE="${LOCAL_TIMEZONE:-America/Fortaleza}"
    BATTERY_LIMIT_HIGH="${BATTERY_LIMIT_HIGH:-75}"
    BATTERY_LIMIT_LOW="${BATTERY_LIMIT_LOW:-60}"
    BATTERY_FORCE_DISCHARGE_ABOVE="${BATTERY_FORCE_DISCHARGE_ABOVE:-82}"
    BATTERY_TEMP_EMERGENCY_TENTHS="${BATTERY_TEMP_EMERGENCY_TENTHS:-400}"
    BATTERY_TEMP_RESUME_TENTHS="${BATTERY_TEMP_RESUME_TENTHS:-370}"
    BATTERY_LOW_ALERT_PCT="${BATTERY_LOW_ALERT_PCT:-20}"
    FRIDGE_RISK_OUTAGE_SEC="${FRIDGE_RISK_OUTAGE_SEC:-14400}"
    SPECTRUM_RSSI_THRESHOLD="${SPECTRUM_RSSI_THRESHOLD:--60}"
    SPECTRUM_PERSIST_MIN_SEC="${SPECTRUM_PERSIST_MIN_SEC:-180}"
    SPECTRUM_ALERT_COOLDOWN_SEC="${SPECTRUM_ALERT_COOLDOWN_SEC:-900}"
    PING_TARGET_PRIMARY="${PING_TARGET_PRIMARY:-1.1.1.1}"
    PING_TARGET_SECONDARY="${PING_TARGET_SECONDARY:-8.8.8.8}"
    CURL_TIMEOUT_SEC="${CURL_TIMEOUT_SEC:-12}"
}
