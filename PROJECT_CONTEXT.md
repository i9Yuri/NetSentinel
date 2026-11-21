# NetSentinel — Documento Mestre de Arquitetura e Contexto Integral

Este documento serve como a especificação técnica primária e fonte de verdade do projeto **NetSentinel** para agentes de IA e desenvolvedores.

---

## 1. Visão Geral e Objetivo do Sistema

O **NetSentinel** é um sistema distribuído, privado e de **custo estrito R$ 0,00** projetado para monitorar continuamente um apartamento remoto em Ponta de Campina (João Pessoa - PB) a partir da workstation principal em Campina Grande - PB e do smartphone principal (Samsung S20 FE).

Ele resolve quatro problemas concretos:
1. **Diferenciação Forense de Quedas:** Distinguir com certeza absoluta entre falta de energia elétrica no apartamento (risco para geladeira/congelador) e queda isolada do provedor de internet (ISP), mesmo sem chip/dados móveis no nó remoto.
2. **Dead Man's Switch:** Detectar silêncio de 180 segundos (3 ciclos) no Hub Nobara Linux e alertar imediatamente via KDE (`notify-send -u critical`) e Telegram.
3. **Redundância 24/7 com PC Desligado:** O nó Android envia alertas forenses críticos e atualiza um *Status Card* fixado diretamente na Telegram Bot API via HTTPS.
4. **Preservação de Hardware e Auditoria Passiva de Espectro:** Mantém a bateria do Moto E7 Plus em modo bypass real (`0 uA`) entre 60%–75% (com corte térmico a 40.0 °C) e monitora anomalias de proximidade Wi-Fi/LAN/BT contra whitelist.

---

## 2. Mapa Estrutural do Repositório

```text
NetSentinel/
├── deploy_to_phone.sh               # Script de push ADB que instala e reinicia o serviço no Android
├── device_root/
│   ├── aegis_daemon.py              # Auxiliar Python / servidor local do painel
│   ├── aegis_daemon.sh              # Daemon residente de baixíssimo consumo (< 15 MB RAM)
│   ├── config.json                  # Tokens do Telegram, IPs de ping e MAC do Xeon
│   ├── modules/
│   │   ├── power_watchdog.sh        # Listener de interrupções de VBUS / status da bateria
│   │   ├── network_auditor.sh       # Medidor de latência, jitter, perda de pacotes e DNS
│   │   ├── arp_sentinel.sh          # Scanner passivo de novos dispositivos na rede local
│   │   └── wol_bridge.sh            # Emissor de pacotes UDP Wake-on-LAN para o Xeon
│   └── data/
│       └── metrics.db               # SQLite local armazenando série temporal de disponibilidade
├── host_tools/
│   ├── generate_report.py           # Converte o metrics.db do celular em relatório PDF/HTML para Anatel
│   └── dashboard/                   # Interface web Bento Grid escuro para métricas de energia e rede
├── .env.example                       # Template de segredos do Hub (copiar para .env com chmod 0600)
├── .gitignore                         # Bloqueia .env, node.env, .venv, *.db, *.log
├── pyproject.toml                     # Empacotamento Python (zero deps em runtime; pytest em [dev])
├── ENVIRONMENT_REPORT.md              # Sondagem empírica validada (Nobara 44 + Moto E7 Plus LineageOS 24.0)
├── ARCHITECTURE.md                    # Diagramas Mermaid, máquina de estados, protocolo HMAC e bypass PMI632
├── PROJECT_CONTEXT.md                 # Este documento mestre de contexto
├── README.md                          # Guia operacional completo de setup, deploy e uso
├── CHANGELOG.md                       # Histórico de versões
├── node_android/
│   ├── README_NODE.md                 # Referência dos módulos POSIX no Moto E7 Plus
│   ├── service.d/
│   │   └── 99-netsentinel.sh          # Launcher persistente de boot (/data/adb/service.d/)
│   └── netsentinel/
│       ├── node.env.example           # Configuração do nó remoto (/data/adb/netsentinel/node.env 0600)
│       ├── whitelist.conf             # MACs/BSSIDs conhecidos da vizinhança e do apartamento
│       ├── oui_table.tsv              # Tabela local offline de fabricantes por prefixo OUI
│       ├── lib_common.sh              # Escrita atômica, HMAC-SHA256 POSIX puro, wakelocks, JSON escape
│       ├── battery_limiter.sh         # Controlador PMI632 (bypass 60-75% + corte térmico 40.0 °C)
│       ├── power_blackbox.sh          # Caixa-preta atômica (blackbox.log) + máquina AC vs ISP
│       ├── spectrum_auditor.sh        # Auditor passivo Wi-Fi (cmd wifi/iw) + LAN (ip neigh) + BT cache
│       ├── telegram_client.sh         # Cliente direto Telegram Bot API + Status Card fixado
│       └── netsentinel_node.sh        # Daemon principal (Normal 60s / Sobrevivência 120s)
├── hub_linux/
│   ├── systemd/
│   │   └── netsentinel-hub.service    # Unit systemd --user (MemoryMax=40M, CPUQuota=5%)
│   └── netsentinel_hub/
│       ├── __init__.py
│       ├── config.py                  # Loader tipado .env + auditoria de permissão 0600
│       ├── security.py                # HMAC-SHA256 em tempo constante + anti-replay 300s
│       ├── models.py                  # Dataclasses tipadas (Heartbeat, BlackboxEvent, SpectrumAnomaly)
│       ├── state_machine.py           # Dead Man's Switch + laudos forenses + deduplicação por cooldown
│       ├── storage.py                 # SQLite WAL (synchronous=FULL) + runtime_state.json atômico
│       ├── notifier.py                # KDE notify-send + Telegram Bot API via urllib stdlib
│       ├── server.py                  # Servidor HTTP dual-stack IPv4/IPv6 + thread watchdog
│       └── cli.py                     # CLI: run | status | audit | test-alert | verify-env
├── scripts/
│   ├── probe_e7plus.sh                # Diagnóstico empírico seguro do Moto E7 Plus via ADB + root
│   ├── deploy_adb.sh                  # Deploy via ADB para /data/adb/netsentinel e service.d + smoke test
│   └── setup_hub.sh                   # Cria .venv via uv, gera .env 0600 e instala systemd --user
└── tests/
    ├── test_hmac_compat.py            # Validação cruzada bit-a-bit POSIX HMAC vs Python stdlib
    ├── test_battery_limiter.py        # Histerese 60-75%, bypass, descarga >82%, corte térmico 40°C
    ├── test_power_state_machine.py    # Queda elétrica real, queda ISP, Dead Man's Switch, deduplicação
    ├── test_spectrum_parser.py        # Parser Wi-Fi/LAN, whitelist, persistência RSSI, OUI, cooldown
    └── test_hub_integration.py        # HTTP end-to-end, 401 HMAC inválido, SQLite WAL e CLI
```

---

## 3. Pipeline / Fluxo de Funcionamento Passo a Passo

1. **Ciclo do Nó Android (`netsentinel_node.sh`):**
   - Adquire partial wakelock (`netsentinel_cycle_wl`) e garante `oom_score_adj = -1000`.
   - Executa `apply_battery_policy` (`battery_limiter.sh`): valida temperatura (`>= 400` -> corte térmico), aplica bypass (`charging_enabled=0`) em `75%–81%`, descarga suave (`input_suspend=1`) em `>= 82%` e recarga (`charging_enabled=1`) em `<= 60%`.
   - Lê `usb/present`, `usb/online`, `pc_port/online` e `usb/voltage_now` para determinar presença real de energia AC independentemente do limitador de carga.
   - Testa conectividade externa (`ping 1.1.1.1` / `8.8.8.8`) e lê RSSI via `iw wlan0 link` / `dumpsys wifi`.
   - Avança `power_blackbox_step`: grava eventos `POWER_LOST`, `POWER_RESTORED` (`REAL_POWER_OUTAGE`), `ISP_RESTORED` (`ISP_ONLY_OUTAGE`) e `LOW_BATTERY_OUTAGE` com `fsync` em `blackbox.log` e fila pendente.
   - Executa `run_spectrum_audit`: escaneia Wi-Fi (`cmd wifi start-scan` + `list-scan-results`), LAN (`ip neigh`) e cache BT (`dumpsys bluetooth_manager`), filtrando whitelist e detectando persistência `>= 180s` com `RSSI >= -60 dBm` ou novo peer na LAN.
   - Se online: envia heartbeat JSON assinado via HMAC-SHA256 ao Hub (se `HUB_ENDPOINT_URL` configurado), despacha alertas forenses/intrusão ao Telegram e atualiza o Status Card fixado a cada 5 min; limpa filas pendentes somente após entrega confirmada.
   - Se sem energia: apaga a tela (`input keyevent 223`), libera wakelock e dorme `120s` em Modo Sobrevivência (Deep Sleep).
2. **Ciclo do Hub Nobara (`netsentinel-hub`):**
   - Escuta `POST /api/v1/heartbeat` (dual-stack IPv4/IPv6) e `GET /health`.
   - Verifica `X-NetSentinel-Signature` em tempo constante e janela temporal `<= 300s`.
   - Grava telemetria e eventos no SQLite WAL e atualiza `runtime_state.json` atomicamente.
   - Thread watchdog executa a cada `10s`: se `now - last_received_at >= 180s`, dispara alerta crítico no KDE (`notify-send -u critical`) e no Telegram com deduplicação.

---

## 4. Restrições Críticas de Hardware, SO e Ambiente

- **Moto E7 Plus (`guam`, Serial `ZF52366RHQ`):**
  - **ROM/Root validados:** LineageOS 24.0 (`24.0-20261112-NIGHTLY-guam`, Android 17 / SDK 37, sem Google Play Services) + Magisk v31.1 (`uid=0`, `/data/adb/service.d/`).
  - **Conectividade:** Apenas Wi-Fi **2.4 GHz** (802.11 b/g/n) e Bluetooth 5.0 (sem dados móveis/chip; microfone físico desconectado).
  - **PMIC PMI632 (`qcom,qpnp-smb5`):** Em porta USB SDP, `usb/online` fica `0` enquanto `usb/present=1` e `pc_port/online=1`. Nunca usar apenas `usb/online` para detectar queda de luz.
  - **Bluetooth Android HAL:** Não existem `/dev/hci*` ou BlueZ (`btmgmt`/`hcitool`); o espectro principal vem de Wi-Fi (`cmd wifi`/`iw`) e LAN (`ip neigh`), complementado pelo cache do `dumpsys bluetooth_manager`.
  - **Sem Python/OpenSSL no aparelho:** Scripts do nó devem permanecer 100% POSIX Shell (`/system/bin/sh` + `toybox` + `curl 8.16.0-DEV` + `sha256sum` + `xxd`).
- **Central Nobara Linux 44 (KDE Plasma / Wayland):**
  - Python `3.14.8` stdlib pura (`sqlite3 3.51.2`), `uv`, `notify-send 0.8.9`, `systemctl --user`.
  - Teto estrito do serviço: `< 30 MB` RAM (enforced `MemoryMax=40M`), `< 0.5%` CPU.
  - Host em NAT IPv4 (`192.168.0.14`) com IPv6 global + `firewalld` ativo e operação não-24/7.

---

## 5. Configurações Globais e Dependências

- **Dependências de Runtime (Hub):** Nenhuma externa — 100% biblioteca padrão do Python 3.11+.
- **Dependências de Desenvolvimento:** `pytest>=8.0` instalado em `.venv` via `uv`.
- **Segredos (nunca comitar):**
  - Hub Linux: `.env` na raiz (`chmod 0600`) baseado em [`.env.example`](.env.example).
  - Nó Android: `/data/adb/netsentinel/node.env` (`chmod 0600`, `root:root`) baseado em [`node_android/netsentinel/node.env.example`](node_android/netsentinel/node.env.example).

---

## 6. Instruções Críticas para IAs que Modificarem Este Código

- **O que NUNCA fazer:**
  1. Nunca usar `adb` sem `-s ZF52366RHQ` (o Galaxy S20 FE `RX8R701RLHK` pode estar conectado simultaneamente!).
  2. Nunca adicionar dependências Python pesadas (FastAPI, requests, aiohttp, etc.) ao `hub_linux` — manter stdlib pura para respeitar o teto `< 30 MB` RAM.
  3. Nunca usar `battery/status == Discharging` isoladamente como sinal de queda de energia, nem depender de `usb/online` sem `usb/present`.
  4. Nunca ativar ou referenciar captura de áudio/microfone no Moto E7 Plus (microfone fisicamente inoperante).
  5. Nunca logar `TELEGRAM_BOT_TOKEN` ou `NETSENTINEL_HMAC_SECRET` em stdout, stderr ou SQLite.
  6. Nunca usar `echo ... > arquivo` direto em arquivos de estado persistente; sempre usar `atomic_write` (Shell) ou `atomic_write_json` (Python).
- **Comandos Exatos de Teste e Operação:**
  - Suíte completa de testes: `.venv/bin/pytest -v`
  - Sondagem empírica do aparelho: `./scripts/probe_e7plus.sh ZF52366RHQ`
  - Setup do Hub Linux: `./scripts/setup_hub.sh`
  - Deploy no Moto E7 Plus: `./scripts/deploy_adb.sh ZF52366RHQ`
  - CLI do Hub: `.venv/bin/netsentinel-hub status | audit | verify-env | test-alert | run`

---

## 7. Histórico de Versões e Decisões Críticas Recentes

- **v1.4.2 (2026-11-18):**
  - **Erradicação Definitiva de Alertas Repetitivos de Dispositivos Conectados:**
    - Diagnosticada e corrigida a causa raiz de spam: a máquina de estados e o rastreador de espectro tratavam a detecção de dispositivos LAN como anomalia recorrente com rearmamento a cada 15 min (cooldown), disparando alertas contínuos do mesmo aparelho o dia inteiro.
    - Implementada política de disparo único (`one-shot`): um dispositivo na rede local só emite aviso de "Novo dispositivo conectado" no primeiro avistamento; uma vez alertado (`last_alert_ts > 0`), ele passa a ser reconhecido e não re-alerta.
    - Janela de retenção do rastreador expandida de 300s para 24 horas (86400s) para aparelhos LAN: impede que celulares em repouso profundo (doze mode) que desaparecem momentaneamente da tabela ARP sejam tratados como "novos invasores" ao acordarem.
    - Blindagem na persistência SQLite (`lan_devices`): dispositivos marcados como conhecidos (`is_known = 1`) mantêm seu status perenemente, e o Hub descarta proativamente anomalias de LAN para qualquer MAC já homologado.
    - Tabela OUI atualizada com prefixos secundários da Smart TV GSD (`38:9b:73`) e whitelist doméstica expandida para 13 dispositivos.
- **v1.4.0 (2026-11-18):**
  - Implementado Raio-X Investigativo Não Intrusivo (`/investigar <IP/MAC>`): módulo nativo `investigator.py` com sondagem de portas e diagnóstico preditivo (Câmeras IP RTSP, Smart TVs, Compartilhamento Windows/SMB, Servidores Web/IoT e dispositivos silenciosos).
  - Implementado Sistema de Quarentena e Modo Sentinela Hostil (`/bloquear` e `/desbloquear`): tabela SQLite `quarantine_devices`, sincronização com `blacklist.conf` no Moto E7 Plus e alarme em tempo real no Telegram caso o intruso se reconecte.
  - Implementado Dossiê Cirúrgico de Banimento Físico no Roteador (fornecendo MAC formatado e link direto para o painel de controle do roteador `http://192.168.0.1`).
- **v1.3.2 (2026-11-18):**
  - **Silenciamento de Notificações do Desktop (`ENABLE_DESKTOP_NOTIFY=0`):**
    - Desativação completa de popups intrusivos na tela do PC via `notify-send`. Todos os alertas forenses e avisos operam com exclusividade e sobriedade no Telegram.
  - **Menu de Comandos Nativo & Botão "Menu" Azul (`setChatMenuButton`):**
    - Ativação do botão oficial "Menu" no Telegram Desktop (PC) e no mobile via `setChatMenuButton` com `type="commands"`.
    - Inclusão do `/start` no topo de `BOT_COMMANDS_MENU` com descrições curtas e didáticas, permitindo acionamento com um clique do mouse sem necessidade de digitação.
    - Teclado inline (`INLINE_QUICK_MENU`) com tratamento assíncrono de `callback_query` para respostas imediatas ao toque no chat do desktop.
- **v1.3.1 (2026-11-18):**
  - Eliminação de duplicações na resposta do radar: partição estrita entre `🟢 Dispositivos Conhecidos da Casa` e `🟡 Dispositivos Novos / Não Cadastrados`, garantindo que aparelhos confiáveis nunca mais sejam listados como suspeitos.
  - Sanitização de rótulos: nomes limpos sem duplicação de fornecedor em parênteses.
  - Implementado comando `/confiar` (e `/memorizar`) no Telegram Bot para cadastrar todos os dispositivos atuais como confiáveis.
  - Whitelist de residência cadastrada e homologada para os 10 dispositivos do ecossistema doméstico em Campina Grande.
- **v1.3.0 (2026-11-18):**
  - **Detecção Inteligente de Localização Geográfica:**
    - O sistema detecta autonomamente se o aparelho está em **Casa Campina Grande (Quarto)** ou no **Apê João Pessoa (Ponta de Campina)** via correlação de SSID (`brisa-1082152`), BSSID do gateway (`24:2f:d0:76:41:b4`) e consulta GeoIP (`http://ip-api.com/json`), adaptando o título dos relatórios, comandos do bot e alertas em tempo real.
  - **Varredura Subnet Completa & Descoberta Real de Todos os Dispositivos:**
    - Substituição do ping broadcast único (frequentemente ignorado por TVs, PCs e smartphones modernos) por varredura paralela ultrarrápida em toda a sub-rede `/24` (`ping -c 1 -W 1` em lote assíncrono). Todos os 10+ dispositivos da residência (cabo, Wi-Fi, Alexa, Smart TVs, Soundbars, celulares) agora povoam o kernel ARP em menos de 2 segundos.
    - Correção crítica no parser do TelegramBotWorker: o botão `🛡️ Dispositivos na Rede` colidia com a regra de busca de `rede` (internet), desviando incorretamente para o comando de Wi-Fi; a precedência estrita foi corrigida.
  - **Categorização Inteligente de Rede (`/radar` / `🛡️ Dispositivos na Rede`):**
    - Separação estrita em duas seções solicitadas:
      1. `🟢 Dispositivos Conectados Atualmente (Online agora)`: Lista todos os aparelhos ativos na rede local do apartamento.
      2. `🟡 Dispositivos Novos / Não Cadastrados`: Destaca invasores ou novos aparelhos que entraram na rede recentemente. Se todos forem conhecidos, emite garantia visual de segurança.
    - Motor heurístico inteligente de classificação de hardware (`classify_device` em POSIX Shell e Python):
      - 🌐 **Roteador / Repetidor de Wi-Fi** (Gateway / Intelbras / ZTE / TP-Link / Huawei)
      - 📺 **Smart TV & Streaming** (Samsung TV / LG webOS / Roku / Fire TV / Chromecast)
      - 📱 **Smartphone / Tablet** (Apple iPhone / Samsung Galaxy / Motorola / Xiaomi)
      - 📱 **Dispositivo Móvel com Privacidade Ativa** (Detecção heurística de MAC localmente administrado/aleatório — segundo nibble `2, 6, a, e` de iOS 14+ e Android 10+)
      - 💡 **Casa Inteligente & IoT** (Espressif ESP8266/ESP32, Sonoff, Tuya, Philips Hue)
      - 💻 **Computador / Notebook** (Dell, Lenovo, HP, Intel, Realtek, Microsoft)
      - 🛡️ **Celular Vigia (Moto E7 Plus)** (Identificação determinística do próprio nó)
    - Novo contrato de telemetria `LanDevice` incorporado ao `HeartbeatPayload` e persistido em tabela SQLite dedicada `lan_devices`.
  - **Inteligência Preditiva em Todas as Pontas do Projeto:**
    - `❄️ Geladeira & Luz`: Estimativa da curva de perda térmica dos alimentos (4 a 6 horas de frio retido na geladeira; 24 a 36 horas no congelador com portas fechadas), contagem regressiva em caso de falta de luz, autonomia da bateria do vigia durante o apagão e índice de estabilidade elétrica (0-100).
    - `🔋 Bateria & Hardware`: Cálculo de desgaste evitado em ciclos de recarga pelo bypass PMI632, conferência de tensão de célula segura (< 4.2V vs estresse de 4.4V) e estimativa indireta da temperatura ambiente do cômodo do apê a partir dos sensores estabilizados em repouso.
    - `🌐 Sinal Wi-Fi & Internet`: Score de estabilidade do provedor (0-100), diagnóstico de fluidez para Streaming 4K / Chamadas de Vídeo / Latência local e explicação didática da independência entre rede e energia.
    - `📊 Relatório 24h`: Resumo executivo em narrativa humana sóbria para qualquer familiar leigo, com Índice de Estabilidade Global do Apê (0-100).
    - `⚡ Status do Apê`: Diagnóstico geral de 3 segundos logo no topo da mensagem.
  - Suíte de testes expandida para 20 testes automatizados (`pytest`) com 100% de aprovação.
- **v1.2.0 (2026-11-18):**
  - Implementado `TelegramBotWorker` bidirecional em Python stdlib com teclado interativo persistente de botões clicáveis (`ReplyKeyboardMarkup`) e registro oficial de menu no Telegram (`setMyCommands`), permitindo operação completa por toque sem digitação para usuários leigos.
  - Comandos e botões interativos: `/status`, `/geladeira`, `/bateria`, `/internet`, `/radar`, `/relatorio`, `/historico`, `/ping`, `/alerta`, `/ajuda`.
  - Adicionado cálculo analítico de 24h em SQLite WAL (`fetch_stats_24h`) para relatórios executivos sob demanda.
  - Adicionado alerta imediato de tomada sem energia (`POWER_LOST`) disparado em tempo real se o carregador for despludado enquanto o Wi-Fi estiver ativo.
  - Watchdog de autorrecuperação Wi-Fi (`cmd wifi set-wifi-enabled enabled`) após 5 min sem conexão em tomada normal.
  - Backoff adaptativo do nó Android em relação ao Hub (`--connect-timeout 4`, retentativas a cada 5 min se inacessível) e rotação de `daemon.log` para blindar a memória eMMC.
  - Adicionado assistente de conectividade remota `scripts/setup_remote_tailscale.sh`.
  - Suíte de testes expandida para 19 testes automatizados (`pytest`) com 100% de sucesso.
- **v1.1.0 (2026-11-18):**
  - Humanização integral de todas as mensagens e alertas no Telegram (horário de Brasília `America/Fortaleza`, conversão de RSSI para qualidade legível, status sem jargões de engenharia).
  - Configuração `SPECTRUM_SCAN_WIFI_APS=0` (padrão): erradicação de falsos alarmes causados por repetidores de tomada e Wi-Fi de vizinhos, mantendo auditoria de invasores na LAN (`ip neigh`).
  - Hierarquia de despacho e anti-spam: se Hub está online, ele assume notificações e o nó limpa a fila sem duplicatas no Telegram; se Hub está offline, nó envia diretamente e limpa fila imediatamente, eliminando reenvios em loop.
  - Blindagem de boot: loop supervisor em `/data/adb/service.d/99-netsentinel.sh` (reinício em 5s caso caia) e `systemd --user` com `Restart=always` + `loginctl enable-linger` para inicialização imediata no boot do PC.
  - Sincronização automatizada e segura do `.env` local para o Moto E7 Plus via `scripts/deploy_adb.sh`.
- **v1.0.0 (2026-11-18):**
  - Fase 0 empírica detectou Stock Android 10 sem root no Moto E7 Plus; migrado para LineageOS 24.0 (`2026-11-12`) + Magisk v31.1.
  - Validado empiricamente modo bypass real (`charging_enabled=0` -> `0 uA`) no PMIC PMI632 e desacoplamento via `usb/present`.
  - Implementados `node_android/` (POSIX Shell puro + HMAC RFC 2104), `hub_linux/` (Python stdlib + SQLite WAL + systemd), scripts de automação e suíte `pytest` completa.
