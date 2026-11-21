#!/system/bin/sh
# NetSentinel — Magisk / KernelSU-Next boot service launcher
# Instalado em: /data/adb/service.d/99-netsentinel.sh (chmod 0755)

MODDIR="/data/adb/netsentinel"
DAEMON="${MODDIR}/netsentinel_node.sh"
PIDFILE="${MODDIR}/netsentinel.pid"
BOOTLOG="${MODDIR}/boot.log"

# Aguarda conclusão do boot do Android (sys.boot_completed=1)
wait_count=0
while [ "$(getprop sys.boot_completed 2>/dev/null)" != "1" ]; do
    sleep 2
    wait_count=$((wait_count + 1))
    [ "$wait_count" -ge 90 ] && break
done

# Estabiliza rede e subsistema power_supply
sleep 10

mkdir -p "$MODDIR"

if [ ! -x "$DAEMON" ]; then
    echo "$(date -u +%s) [ERROR] Daemon não encontrado ou sem permissão: $DAEMON" >> "$BOOTLOG"
    exit 1
fi

# Evita duplicação de instância
if pgrep -f "netsentinel_node.sh" >/dev/null 2>&1; then
    echo "$(date -u +%s) [INFO] NetSentinel já em execução" >> "$BOOTLOG"
    exit 0
fi

if [ -f "$PIDFILE" ]; then
    old_pid=$(cat "$PIDFILE" 2>/dev/null || true)
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
        echo "$(date -u +%s) [INFO] NetSentinel já em execução (PID $old_pid)" >> "$BOOTLOG"
        exit 0
    fi
fi

# Impede congelamento por Doze e mantém prioridade do daemon
dumpsys deviceidle disable >/dev/null 2>&1 || true
cmd deviceidle whitelist +com.android.shell >/dev/null 2>&1 || true
settings put system screen_off_timeout 15000 >/dev/null 2>&1 || true

# Inicia daemon em loop supervisor desacoplado da sessão de boot (auto-reinício em 5s se cair)
setsid sh -c "while true; do \"$DAEMON\" >> \"${MODDIR}/daemon.log\" 2>&1; sleep 5; done" </dev/null >/dev/null 2>&1 &
echo "$(date -u +%s) [INFO] NetSentinel supervisor iniciado pelo service.d" >> "$BOOTLOG"
