#!/usr/bin/env bash
# NetSentinel — Setup do Hub no Nobara Linux (.venv via uv + .env 0600 + systemd --user)
# Uso: ./scripts/setup_hub.sh [--enable-service]

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENABLE_SVC=0

for arg in "$@"; do
    [ "$arg" = "--enable-service" ] && ENABLE_SVC=1
done

cd "$REPO_ROOT"

echo "[1/4] Criando ambiente virtual isolado com uv..."
if command -v uv >/dev/null 2>&1; then
    uv venv .venv --python python3
    uv pip install --python .venv/bin/python -e ".[dev]"
else
    python3 -m venv .venv
    .venv/bin/pip install -e ".[dev]"
fi

echo "[2/4] Verificando .env com permissões 0600..."
if [ ! -f "${REPO_ROOT}/.env" ]; then
    cp "${REPO_ROOT}/.env.example" "${REPO_ROOT}/.env"
    SECRET=$(openssl rand -hex 32)
    sed -i "s|CHANGE_ME_GENERATE_WITH_OPENSSL_RAND_HEX_32|${SECRET}|g" "${REPO_ROOT}/.env"
    chmod 0600 "${REPO_ROOT}/.env"
    echo "      .env criado com NETSENTINEL_HMAC_SECRET gerado automaticamente (0600)."
    echo "      Preencha TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID em ${REPO_ROOT}/.env."
else
    chmod 0600 "${REPO_ROOT}/.env"
    echo "      .env existente mantido (permissões garantidas em 0600)."
fi

echo "[3/4] Instalando unit systemd --user..."
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"
cp -f "${REPO_ROOT}/hub_linux/systemd/netsentinel-hub.service" "${SYSTEMD_USER_DIR}/netsentinel-hub.service"
systemctl --user daemon-reload

if [ "$ENABLE_SVC" -eq 1 ]; then
    echo "[4/4] Habilitando e iniciando netsentinel-hub.service..."
    systemctl --user enable --now netsentinel-hub.service
    systemctl --user status netsentinel-hub.service --no-pager || true
else
    echo "[4/4] Unit instalada em ${SYSTEMD_USER_DIR}/netsentinel-hub.service."
    echo "      Para ativar após preencher o .env: systemctl --user enable --now netsentinel-hub.service"
fi

echo "Setup do NetSentinel Hub concluído."
