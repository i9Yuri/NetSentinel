# NetSentinel

Sistema distribuído, privado e **100% gratuito (`R$ 0,00`)** de monitoramento de energia elétrica, saúde de rede e auditoria passiva de espectro Wi-Fi/LAN para um apartamento remoto, rodando em um **Motorola Moto E7 Plus** (LineageOS 24.0 + Magisk v31.1) reportando para uma central no **Nobara Linux (KDE Plasma)** e para o **Telegram** (redundância 24/7 mesmo com o PC desligado).

---

## Recursos Principais

- **Modo Bypass Real da Bateria 24/7 (PMIC PMI632):** Trava a carga entre `60%` e `75%` usando `charging_enabled=0` (`0 uA` na bateria, sem micro-ciclos nem estufamento) e aplica corte térmico de emergência em `>= 40.0 °C`.
- **Caixa-Preta Forense de Queda de Energia vs ISP:** Grava eventos com escrita atômica + `fsync` em `/data/adb/netsentinel/blackbox.log` e distingue com certeza entre **Queda Elétrica Real** (com alerta de risco para geladeira/congelador acima de 4h) e **Queda Apenas de Provedor (ISP)**.
- **Modo de Sobrevivência Ultra-Low Power:** Ao perder energia da tomada, apaga a tela, libera wakelocks entre ciclos (permitindo Deep Sleep real) e espaça a varredura para 120s.
- **Radar Passivo de Espectro:** Combina `cmd wifi start-scan`/`list-scan-results`, `ip neigh` (LAN) e cache `dumpsys bluetooth_manager` com whitelist local, detecção de persistência por proximidade (`RSSI >= -60 dBm` por `>= 3 min`), resolução offline de fabricante (OUI) e cooldown anti-spam.
- **Dead Man's Switch + Redundância PC Off:**
  - No **Nobara Linux**: daemon `systemd --user` em Python stdlib (`< 25 MB` RAM) recebe heartbeats assinados por **HMAC-SHA256** e alerta via `notify-send -u critical` + Telegram após 180s de silêncio.
  - No **Moto E7 Plus**: envia alertas críticos diretamente para a Telegram Bot API e atualiza in-place um **Status Card fixado** a cada 5 minutos.

---

## Pré-requisitos

1. **Nobara Linux:** Python 3.11+, `uv`, `adb`, `notify-send`, `systemd --user`.
2. **Motorola Moto E7 Plus (`guam`):** LineageOS 24.0 + Magisk v31.1 (ou KernelSU-Next) com acesso `su` concedido ao `Shell`.
3. **Bot do Telegram (R$ 0,00):**
   - Crie um bot em `@BotFather` (`/newbot`) para obter `TELEGRAM_BOT_TOKEN`.
   - Envie uma mensagem `/start` para o seu bot e obtenha seu `TELEGRAM_CHAT_ID` via `@userinfobot` (ou `https://api.telegram.org/bot<TOKEN>/getUpdates`).

---

## Instalação Rápida

### 1. Configurar a Central no Nobara Linux

```bash
./scripts/setup_hub.sh
```

Edite o arquivo `.env` gerado (já protegido com `chmod 0600`) e preencha `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`. Valide e ative o serviço:

```bash
.venv/bin/netsentinel-hub --env .env verify-env
.venv/bin/netsentinel-hub --env .env test-alert
systemctl --user enable --now netsentinel-hub.service
```

### 2. Implantar o Nó Sentinela no Moto E7 Plus

Conecte o aparelho via USB (ou ADB) e execute:

```bash
./scripts/deploy_adb.sh ZF52366RHQ
```

Configure `/data/adb/netsentinel/node.env` no aparelho com o mesmo `NETSENTINEL_HMAC_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` e (opcionalmente) `HUB_ENDPOINT_URL`:

```bash
adb -s ZF52366RHQ shell su -c 'chmod 0600 /data/adb/netsentinel/node.env'
```

### 3. Aprender a Whitelist no Dia 1 no Apartamento

Quando posicionar o Moto E7 Plus definitivamente no apartamento em João Pessoa, fotografe os roteadores fixos da vizinhança e equipamentos conhecidos:

```bash
adb -s ZF52366RHQ shell su -c '/data/adb/netsentinel/spectrum_auditor.sh --learn-whitelist'
```

Para iniciar o daemon imediatamente (sem precisar reiniciar o aparelho):

```bash
adb -s ZF52366RHQ shell su -c '/data/adb/service.d/99-netsentinel.sh'
```

---

## Operação e Auditoria via CLI

```bash
# Status resumido do nó monitorado
.venv/bin/netsentinel-hub --env .env status

# Auditoria JSON dos últimos heartbeats, quedas, anomalias de espectro e alertas
.venv/bin/netsentinel-hub --env .env audit --limit 20

# Sondagem completa do hardware do Moto E7 Plus
./scripts/probe_e7plus.sh ZF52366RHQ
```

---

## Testes Automatizados

```bash
.venv/bin/pytest -v
```

Documentação complementar:
- [ARCHITECTURE.md](ARCHITECTURE.md) — Diagramas, máquina de estados e protocolo HMAC.
- [ENVIRONMENT_REPORT.md](ENVIRONMENT_REPORT.md) — Resultados empíricos da Fase 0.
- [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) — Especificação mestra de arquitetura e restrições.
