#!/usr/bin/env bash
# NetSentinel — Guia e Verificador de Conectividade Remota via Tailscale (Custo R$ 0,00)
# Conecta a Workstation em Campina Grande ao Moto E7 Plus em João Pessoa através
# de uma rede mesh WireGuard criptografada, sem abrir portas no roteador nem pagar nada.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

echo "===================================================================="
echo " NetSentinel — Assistente de Conectividade Remota João Pessoa ↔ CG"
echo "===================================================================="
echo ""
echo "O NetSentinel funciona 100% autônomo via Telegram no celular mesmo"
echo "sem este passo. O Tailscale serve apenas se você quiser que o seu PC"
echo "em Campina Grande também receba os relatórios forenses diretos do apê."
echo ""

# 1. Checa Tailscale no Linux
if command -v tailscale >/dev/null 2>&1; then
    echo "[OK] Tailscale CLI encontrado no PC."
    ts_status=$(tailscale status 2>&1 || true)
    if echo "$ts_status" | grep -q "100\."; then
        pc_ip=$(tailscale ip -4 2>/dev/null || echo "")
        echo "[OK] PC conectado ao Tailscale com IP: ${pc_ip}"
    else
        echo "[AVISO] Tailscale instalado mas não conectado. Execute: sudo tailscale up"
    fi
else
    echo "[INFO] Tailscale não instalado no Nobara Linux."
    echo "       Para instalar (gratuito):"
    echo "       sudo dnf install -y tailscale && sudo systemctl enable --now tailscaled && sudo tailscale up"
fi

echo ""
echo "--------------------------------------------------------------------"
echo "Passos para o Moto E7 Plus em João Pessoa:"
echo "--------------------------------------------------------------------"
echo "1. No Moto E7 Plus, instale o Tailscale (via Aurora Store / F-Droid / APK)"
echo "2. Faça login na mesma conta do seu PC."
echo "3. Copie o IP do seu PC no Tailscale (ex: 100.x.y.z)."
echo "4. No arquivo .env do PC, altere a linha:"
echo "   HUB_ENDPOINT_URL=\"http://100.x.y.z:8765/api/v1/heartbeat\""
echo "5. Execute: ./scripts/deploy_adb.sh ZF52366RHQ"
echo ""
echo "Pronto! O celular enviará sinais diretamente para o seu PC em qualquer"
echo "lugar do mundo com zero custo e sem precisar configurar portas no modem."
echo "===================================================================="
