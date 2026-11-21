#!/usr/bin/env bash
# NetSentinel — Deploy automatizado do nó Android (Moto E7 Plus / Magisk ou KernelSU-Next) via ADB
# Uso: ./scripts/deploy_adb.sh [SERIAL] [--start] [--learn-whitelist]

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
SERIAL="${1:-ZF52366RHQ}"
DO_START=0
DO_LEARN=0

for arg in "$@"; do
    case "$arg" in
        --start) DO_START=1 ;;
        --learn-whitelist) DO_LEARN=1 ;;
    esac
done

if ! adb devices | awk '{print $1,$2}' | grep -qx "${SERIAL} device"; then
    echo "ERRO: Dispositivo ${SERIAL} não conectado ou não autorizado via ADB." >&2
    adb devices -l >&2
    exit 1
fi

if ! adb -s "$SERIAL" shell 'su -c id' 2>/dev/null | grep -q "uid=0"; then
    echo "ERRO: Acesso root via 'su' indisponível em ${SERIAL}." >&2
    exit 2
fi

STAGING="/data/local/tmp/netsentinel_stage"
TARGET="/data/adb/netsentinel"
SERVICED="/data/adb/service.d/99-netsentinel.sh"

echo "[1/5] Preparando staging em ${STAGING}..."
adb -s "$SERIAL" shell "rm -rf '${STAGING}' && mkdir -p '${STAGING}'"
adb -s "$SERIAL" push "${REPO_ROOT}/node_android/netsentinel/." "${STAGING}/" >/dev/null
adb -s "$SERIAL" push "${REPO_ROOT}/node_android/service.d/99-netsentinel.sh" "${STAGING}/99-netsentinel.sh" >/dev/null

echo "[2/5] Instalando arquivos em ${TARGET} e ${SERVICED} sob root..."
adb -s "$SERIAL" shell su <<EOF
set -e
mkdir -p "${TARGET}/state" "${TARGET}/logs" /data/adb/service.d
cp -f "${STAGING}"/*.sh "${TARGET}/"
cp -f "${STAGING}/oui_table.tsv" "${TARGET}/oui_table.tsv"
cp -f "${STAGING}/node.env.example" "${TARGET}/node.env.example"
if [ ! -f "${TARGET}/whitelist.conf" ]; then
    cp -f "${STAGING}/whitelist.conf" "${TARGET}/whitelist.conf"
fi
if [ ! -f "${TARGET}/node.env" ]; then
    cp -f "${STAGING}/node.env.example" "${TARGET}/node.env"
    chmod 0600 "${TARGET}/node.env"
fi
cp -f "${STAGING}/99-netsentinel.sh" "${SERVICED}"
chmod 0755 "${TARGET}"/*.sh "${SERVICED}"
chmod 0644 "${TARGET}/oui_table.tsv" "${TARGET}/whitelist.conf" "${TARGET}/node.env.example"
chmod 0600 "${TARGET}/node.env"
chown -R 0:0 "${TARGET}" "${SERVICED}"
rm -rf "${STAGING}"
EOF

# Sincroniza configurações do .env local -> node.env de forma segura
if [ -f "${REPO_ROOT}/.env" ]; then
    echo "[3/5] Sincronizando configurações do .env local para o aparelho..."
    TEMP_ENV="$(mktemp)"
    export REPO_ROOT
    python3 - <<'PY_SYNC' > "$TEMP_ENV"
import os
import re
from pathlib import Path

repo_root = Path(os.environ["REPO_ROOT"])
env_file = repo_root / ".env"
env_data = {}
for line in env_file.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
    if m:
        key, val = m.group(1), m.group(2).strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        env_data[key] = val

template = (repo_root / "node_android" / "netsentinel" / "node.env.example").read_text(encoding="utf-8")
for k, v in env_data.items():
    safe_v = v.replace("\\", "\\\\").replace('"', '\\"')
    template = re.sub(rf'^{k}=.*$', f'{k}="{safe_v}"', template, flags=re.MULTILINE)

print(template)
PY_SYNC

    adb -s "$SERIAL" push "$TEMP_ENV" "/data/local/tmp/node.env.sync" >/dev/null
    rm -f "$TEMP_ENV"
    adb -s "$SERIAL" shell su <<EOF
cp -f /data/local/tmp/node.env.sync "${TARGET}/node.env"
chmod 0600 "${TARGET}/node.env"
chown 0:0 "${TARGET}/node.env"
rm -f /data/local/tmp/node.env.sync
# Limpa filas residuais de anomalias passadas
: > "${TARGET}/state/spectrum_anomalies.jsonl"
EOF
    echo "      Configurações sincronizadas em ${TARGET}/node.env."
else
    echo "[3/5] Nenhum .env local encontrado (usando template node.env no aparelho)."
fi

if [ "$DO_LEARN" -eq 1 ]; then
    echo "[4/5] Executando aprendizado inicial da whitelist de espectro no aparelho..."
    adb -s "$SERIAL" shell "su -c '${TARGET}/spectrum_auditor.sh --learn-whitelist'"
else
    echo "[4/5] Pulando --learn-whitelist (execute no apartamento com: adb -s ${SERIAL} shell su -c '${TARGET}/spectrum_auditor.sh --learn-whitelist')."
fi

echo "[5/5] Reiniciando daemon persistente com novas regras..."
adb -s "$SERIAL" shell su <<'EOF'
cur_pid="$$"
if [ -r /data/adb/netsentinel/netsentinel.pid ]; then
    kill -TERM "$(cat /data/adb/netsentinel/netsentinel.pid)" 2>/dev/null || true
fi
for p in $(pgrep -f "netsentinel_node.sh" 2>/dev/null || true); do
    [ "$p" != "$cur_pid" ] && kill -TERM "$p" 2>/dev/null || true
done
rm -f /data/adb/netsentinel/netsentinel.pid
EOF

# Executa smoke test de 1 ciclo para validar integridade
adb -s "$SERIAL" shell "su -c '${TARGET}/netsentinel_node.sh --once'"

# Inicia o supervisor desacoplado do ADB
adb -s "$SERIAL" shell "su -c '${SERVICED} >/dev/null 2>&1 &'"
echo "Daemon NetSentinel ativo e supervisionado pelo Magisk service.d."

echo "Deploy concluído com sucesso em ${SERIAL}."
