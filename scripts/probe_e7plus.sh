#!/usr/bin/env bash
# NetSentinel — Fase 0: Sondagem Empírica do Motorola Moto E7 Plus (guam) via ADB
# Uso: ./scripts/probe_e7plus.sh [SERIAL]
# Seguro: executa apenas leituras e um teste reversível de wakelock (quando root disponível).

set -u

SERIAL="${1:-ZF52366RHQ}"

if ! command -v adb >/dev/null 2>&1; then
    echo "ERRO: adb não encontrado no PATH." >&2
    exit 1
fi

if ! adb devices | awk '{print $1,$2}' | grep -qx "${SERIAL} device"; then
    echo "ERRO: Dispositivo ${SERIAL} não encontrado ou não autorizado." >&2
    echo "Dispositivos atuais:" >&2
    adb devices -l >&2
    exit 2
fi

SU_BIN=""
HAS_ROOT=0
if adb -s "${SERIAL}" shell 'id' 2>/dev/null | grep -q "uid=0"; then
    HAS_ROOT=1
    SU_BIN="DIRECT_ROOT"
else
    for cand in su /debug_ramdisk/su /sbin/su /system/bin/su /system/xbin/su; do
        if adb -s "${SERIAL}" shell "[ -x '${cand}' ] || command -v '${cand}' >/dev/null 2>&1" >/dev/null 2>&1; then
            if adb -s "${SERIAL}" shell "'${cand}' -c id 2>/dev/null" | grep -q "uid=0"; then
                HAS_ROOT=1
                SU_BIN="${cand}"
                break
            fi
        fi
    done
fi

run_cmd() {
    if [ "${HAS_ROOT}" -eq 1 ]; then
        if [ "${SU_BIN}" = "DIRECT_ROOT" ]; then
            adb -s "${SERIAL}" shell "$1"
        else
            adb -s "${SERIAL}" shell "${SU_BIN} -c '$1'"
        fi
    else
        adb -s "${SERIAL}" shell "$1"
    fi
}

echo "================================================================"
echo "NetSentinel Empirical Probe — Target Serial: ${SERIAL}"
echo "Timestamp (Host): $(date -Iseconds)"
echo "Root via ADB (su): $( [ "${HAS_ROOT}" -eq 1 ] && echo "AVAILABLE (uid=0)" || echo "UNAVAILABLE (running as shell uid=2000)" )"
echo "================================================================"

echo ""
echo "=== 1. DEVICE, OS, KERNEL, BOOTLOADER & ROOT ==="
adb -s "${SERIAL}" shell '
echo "model=$(getprop ro.product.model)"
echo "device=$(getprop ro.product.device)"
echo "abi=$(getprop ro.product.cpu.abi)"
echo "android=$(getprop ro.build.version.release) (SDK $(getprop ro.build.version.sdk))"
echo "lineage=$(getprop ro.lineage.version)"
echo "build_display=$(getprop ro.build.display.id)"
echo "fingerprint=$(getprop ro.build.fingerprint)"
echo "flash.locked=$(getprop ro.boot.flash.locked)"
echo "verifiedbootstate=$(getprop ro.boot.verifiedbootstate)"
echo "vbmeta.device_state=$(getprop ro.boot.vbmeta.device_state)"
echo "uname=$(uname -a)"
echo "uptime=$(cat /proc/uptime)"
echo "date=$(date)"
echo "tz=$(getprop persist.sys.timezone)"
echo "packages_hint:"
pm list packages 2>/dev/null | grep -iE "ksu|kernelsu|magisk|termux|lineage|microg|gms|tailscale|wireguard" || true
'
if [ "${HAS_ROOT}" -eq 1 ]; then
    run_cmd 'echo "su_id=$(id)"; echo "selinux=$(getenforce)"; which su; su -v 2>&1 || true; su -V 2>&1 || true'
else
    adb -s "${SERIAL}" shell 'echo "shell_id=$(id)"; echo "selinux=$(getenforce 2>&1)"; echo "su_binary=$(command -v su 2>&1 || echo NOT_FOUND)"'
fi

echo ""
echo "=== 2. KERNELSU / BOOT PERSISTENCE PATHS ==="
run_cmd '
for p in /data/adb /data/adb/service.d /data/adb/post-fs-data.d /data/adb/ksu /data/adb/ksu/bin /data/adb/modules /data/adb/netsentinel /data/local/tmp; do
    if [ -e "$p" ]; then
        echo "[EXISTS] $p"
        ls -ld "$p" 2>&1
    else
        echo "[MISSING_OR_DENIED] $p"
    fi
done
'

echo ""
echo "=== 3. POWER SUPPLY & BATTERY SYSFS ==="
run_cmd '
echo "--- Symlinks in /sys/class/power_supply ---"
ls -la /sys/class/power_supply/ 2>&1
for node in /sys/class/power_supply/*; do
    [ -d "$node" ] || continue
    echo ""
    echo "--- NODE: $node ---"
    found=0
    for f in "$node"/*; do
        [ -f "$f" ] || continue
        found=1
        base=$(basename "$f")
        case "$base" in
            uevent|subsystem|device|power) continue ;;
        esac
        perm=$(ls -l "$f" 2>/dev/null | awk "{print \$1,\$3,\$4}")
        val=$(head -n 1 "$f" 2>/dev/null | tr -d "\r\n")
        echo "  $base [$perm] = ${val:-<unreadable>}"
    done
    [ "$found" -eq 0 ] && echo "  <directory unreadable or empty without root/SELinux>"
done
echo ""
echo "--- Potential Charging Control Files ---"
find /sys/class/power_supply/ -maxdepth 2 \( -name "*charg*" -o -name "*suspend*" -o -name "*limit*" -o -name "*disable*" -o -name "*enable*" -o -name "*online*" -o -name "*status*" -o -name "*present*" -o -name "*temp*" -o -name "*capacity*" -o -name "*voltage*" -o -name "*current*" \) -exec ls -l {} + 2>/dev/null | sort || true
echo ""
echo "--- dumpsys battery ---"
dumpsys battery
'

echo ""
echo "=== 4. AVAILABLE BINARIES & TLS CAPABILITY ==="
run_cmd '
export PATH="$PATH:/data/adb/ksu/bin:/data/adb/magisk:/data/data/com.termux/files/usr/bin"
for bin in sh ash bash toybox busybox curl wget nc ping ping6 ip iw iwlist wpa_cli sqlite3 python3 python openssl sha256sum awk sed grep cut tr date stat sync fsync timeout nohup setsid hexdump od xxd base64 tar gzip flock crond; do
    p=$(command -v "$bin" 2>/dev/null || true)
    if [ -n "$p" ]; then
        echo "  [OK] $bin -> $p"
    else
        echo "  [--] $bin"
    fi
done
if command -v busybox >/dev/null 2>&1; then
    echo "busybox: $(busybox | head -n 1)"
fi
if command -v toybox >/dev/null 2>&1; then
    echo "toybox: $(toybox --version 2>&1)"
fi
if command -v curl >/dev/null 2>&1; then
    curl --version 2>&1 | head -n 3
    echo "--- HTTPS test (api.telegram.org) ---"
    curl -sI --max-time 5 https://api.telegram.org 2>&1 | head -n 3 || true
fi
'

echo ""
echo "=== 5. WI-FI, LAN & INTERNET CAPABILITY ==="
run_cmd '
echo "--- ip -brief addr (active) ---"
ip -brief addr | grep -vE "DOWN|rmnet_data"
echo "--- cmd wifi status ---"
cmd wifi status 2>&1 | head -n 20 || true
echo "--- cmd wifi list-scan-results ---"
cmd wifi list-scan-results 2>&1 | head -n 20 || true
echo "--- dumpsys wifi (mWifiInfo & scan summary) ---"
dumpsys wifi 2>&1 | grep -E "mWifiInfo |mNetworkInfo |mLastSignalLevel|Latest scan results:" -A 2 | head -n 30 || true
if command -v iw >/dev/null 2>&1; then
    echo "--- iw wlan0 link ---"
    iw wlan0 link 2>&1 || true
    echo "--- iw wlan0 scan dump (sample) ---"
    iw wlan0 scan dump 2>&1 | grep -E "^BSS |signal:|SSID:" | head -n 24 || true
fi
if command -v wpa_cli >/dev/null 2>&1; then
    echo "--- wpa_cli scan_results ---"
    wpa_cli -i wlan0 scan_results 2>&1 | head -n 15 || true
fi
echo "--- ip neigh ---"
ip neigh show 2>&1 || true
echo "--- ping 1.1.1.1 ---"
ping -c 1 -W 3 1.1.1.1 2>&1 || true
'

echo ""
echo "=== 6. BLUETOOTH / BLE CAPABILITY ==="
run_cmd '
ls -la /sys/class/bluetooth/ 2>&1 || true
ls -la /dev/bt* /dev/vhci* /dev/hci* 2>&1 || true
for b in btmgmt hcitool hciconfig bluetoothctl l2ping; do
    command -v "$b" >/dev/null 2>&1 && echo "  [OK] $b" || echo "  [--] $b"
done
echo "bluetooth_on=$(settings get global bluetooth_on)"
echo "ble_scan_always_enabled=$(settings get global ble_scan_always_enabled)"
cmd bluetooth_manager help 2>&1 | head -n 20 || true
dumpsys bluetooth_manager 2>&1 | grep -E "enabled:|state:|address:|name:|Bonded|Scan|Gatt|AdapterProperties|BLE|RSSI" | head -n 30 || true
'

echo ""
echo "=== 7. DEEP SLEEP, DOZE, WAKELOCKS & MEMORY ==="
run_cmd '
ls -la /sys/power/wake_lock /sys/power/wake_unlock 2>&1 || true
if [ -w /sys/power/wake_lock ] && [ -w /sys/power/wake_unlock ]; then
    if echo "netsentinel_probe_wl" > /sys/power/wake_lock 2>&1; then
        grep -o "netsentinel_probe_wl" /sys/power/wake_lock >/dev/null 2>&1 && echo "WAKELOCK_ACQUIRE=OK" || echo "WAKELOCK_ACQUIRE=NOT_VISIBLE"
        echo "netsentinel_probe_wl" > /sys/power/wake_unlock 2>&1 && echo "WAKELOCK_RELEASE=OK" || echo "WAKELOCK_RELEASE=FAIL"
    else
        echo "WAKELOCK_ACQUIRE=FAIL"
    fi
else
    echo "WAKELOCK_TEST=SKIPPED (no write permission on /sys/power/wake_lock)"
fi
dumpsys power 2>&1 | grep -E "mWakefulness=|Display Power:|mHoldingWakeLockSuspendBlocker=" | head -n 10
dumpsys deviceidle 2>&1 | grep -E "mState=|mLightState=|mDeepEnabled=|mLightEnabled=" | head -n 10
head -n 6 /proc/meminfo
cat /proc/swaps 2>&1 || true
'

echo "================================================================"
echo "Probe completed (HAS_ROOT=${HAS_ROOT})."
