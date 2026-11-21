# Changelog

Todas as mudanças relevantes do **NetSentinel** são documentadas neste arquivo.

## [1.5.0] - 2026-11-21

### Adicionado & Aperfeiçoado (Aegis-Sentinel & Fiscal de Rede)
- **Watchdog Elétrico por Transição de VBUS com Debounce:** Módulo `power_watchdog.sh` inspecionando diretamente os nós do PMIC Qualcomm PMI632 (`usb/present`, `usb/online`, `pc_port/online`, `usb/voltage_now`), com debounce de 5s para corte e 10s para restabelecimento de energia elétrica.
- **Fiscal de Qualidade de Internet (Prova Técnica para Anatel):** Módulo `network_auditor.sh` com medição periódica de RTT médio, jitter e perda de pacotes para o gateway, servidores DNS e IX.br/PTT, gravando em série temporal SQLite (`metrics.db`).
- **Ponte de Controle e Wake-on-LAN:** Módulo `wol_bridge.sh` permitindo disparo de Magic Packet Ethernet via broadcast UDP (porta 9) para ligar a placa-mãe do Xeon (`00:c0:c6:25:81:52`), com verificação de resposta ICMP após 15s.
- **Scanner Passivo de Dispositivos:** Módulo `arp_sentinel.sh` monitorando `/proc/net/arp` e `ip neigh` sem gerar tráfego invasivo.
- **Ferramentas de Auditoria e Dashboard Host:** `generate_report.py` para emissão automática de laudos técnicos estruturados (Markdown e HTML) com hash SHA-256 probatório e interface web Bento Grid escuro (`host_tools/dashboard/index.html`).
- **Deploy e Inicialização Persistente:** Script `deploy_to_phone.sh` com suporte a ADB com fio e sem fio (porta 5555), provisionando serviço Magisk em `/data/adb/service.d/aegis_sentinel.sh`.

## [1.4.2] - 2026-11-18

### Corrigido & Blindado
- **Erradicação do Loop de Alertas Falsos de "Novo Dispositivo":**
  - **Causa Raiz Identificada:** O auditor de espectro no celular tratava aparelhos na rede local com a mesma regra de cooldown (15 minutos) usada para redes Wi-Fi vizinhas. Consequentemente, a cada 15 minutos, qualquer aparelho que estivesse conectado e não constasse na whitelist disparava um alerta repetido de "Novo dispositivo conectado". Além disso, celulares que entravam em modo de repouso (doze mode) e sumiam do ARP por mais de 5 minutos eram apagados do rastreador, sendo tratados como "novos invasores" ao acordarem.
  - **Correção no Rastreador do Celular (`spectrum_auditor.sh`):** Alertas de aparelhos na rede local (LAN) agora disparam estritamente uma única vez (`last_alert_ts == 0`). Janela de retenção expandida para 24h (86400s), preservando o histórico de aparelhos móveis mesmo durante pausas de rede.
  - **Filtro no Hub e Persistência Confiável:** Dispositivos marcados como conhecidos (`is_known = 1`) no SQLite não são mais sobrescritos pelo heartbeat. Alertas de LAN para MACs já conhecidos são descartados automaticamente antes do envio.
  - **Atualização da Tabela de Fabricantes e Whitelist:** Adicionado prefixo da interface secundária da Smart TV GSD (`38:9b:73`) e cadastrados todos os 13 dispositivos da casa.

## [1.4.1] - 2026-11-18

### Aprimorado & Otimizado
- **Silenciamento Total das Notificações no Desktop (`ENABLE_DESKTOP_NOTIFY=0`):**
  - Desativados popups intrusivos do KDE via `notify-send`. Todos os alertas operam exclusivamente pelo Telegram, garantindo que o computador fique livre de janelas de notificação sobrepostas na tela.
- **Botão Oficial "Menu" Ativo no Telegram Desktop (PC) & Mobile:**
  - Configurado `setChatMenuButton` com `type="commands"` e adicionado `/start` no topo de `BOT_COMMANDS_MENU`.
  - No Telegram Desktop, o botão azul de pílula **"Menu"** agora abre imediatamente o menu com todos os comandos prontos para clique, exatamente como na interface padrão do Telegram, sem exigir que o usuário digite nada.
  - Adicionado suporte a teclado inline (`INLINE_QUICK_MENU`) com processamento de `callback_query`, permitindo interações por toque direto dentro da mensagem no chat do desktop.

## [1.4.0] - 2026-11-18

### Adicionado & Aperfeiçoado
- **Raio-X Investigativo Não Intrusivo de Dispositivos (`/investigar <IP/MAC>`):**
  - Módulo nativo `investigator.py` (stdlib pura) que executa sondagem TCP ultrarrápida e paralela (< 1s) em portas padronizadas de rede local (80, 443, 554, 445, 139, 8008, 8080, 9100, 22).
  - Diagnóstico heurístico preditivo para identificar a natureza real do aparelho suspeito antes de qualquer ação (Câmeras IP RTSP, Smart TVs/Chromecast, Compartilhamento de arquivos Windows/Samba, Dispositivos Web/IoT ou Smartphones silenciosos).
- **Sistema de Quarentena e Modo Sentinela Hostil (`/bloquear` e `/desbloquear`):**
  - Tabela SQLite WAL `quarantine_devices` e flag `is_blocked` em `lan_devices`.
  - Coloca aparelhos intrusos ou desconhecidos em monitoramento ativo de quarentena.
  - Sincronização automática com `blacklist.conf` no Moto E7 Plus e isolamento de rotas locais.
- **Detecção em Tempo Real e Alarme de Intrusos:**
  - A cada ciclo de heartbeat, o Hub verifica se algum dispositivo presente na quarentena/bloqueado tentou restabelecer comunicação na rede Wi-Fi, disparando alerta sonoro de severidade crítica imediato no Telegram (`category="INTRUDER"`).
- **Dossiê Cirúrgico de Banimento Físico no Roteador:**
  - O bot entrega o endereço MAC formatado pronto para cópia e o link direto do painel do roteador (`http://192.168.0.1`), orientando a expulsão definitiva nas configurações de controle de acesso/filtro MAC.
- **Integração no Menu do Telegram:**
  - Comandos oficiais registrados: `/investigar`, `/bloquear`, `/desbloquear`.
  - Seção dedicada `🔴 Dispositivos em Quarentena / Bloqueados` no comando `/radar`.

## [1.3.1] - 2026-11-18

### Corrigido & Aprimorado
- **Eliminação de Duplicação e Falsos Alarmes no Radar de Rede:**
  - Corrigida a lógica de apresentação de dispositivos: antes, aparelhos não cadastrados na whitelist apareciam simultaneamente na lista geral e na lista de "novos", gerando a sensação de que eletrodomésticos da própria casa estavam sendo alertados repetidamente como intrusos.
  - Implementada partição determinística:
    - `🟢 Dispositivos Conhecidos da Casa`: exibe apenas aparelhos confiáveis cadastrados na residência.
    - `🟡 Dispositivos Novos / Não Cadastrados`: exibe exclusivamente novos dispositivos não reconhecidos. Se todos forem confiáveis, confirma de forma limpa: `✅ Nenhum aparelho desconhecido. Todos os dispositivos na rede são conhecidos e confiáveis da sua casa.`
- **Sanitização de Rótulos e Nomes Amigáveis:**
  - Eliminadas repetições desnecessárias de fabricante entre parênteses (ex: `Amazon Echo / Alexa (Amazon (Echo/FireTV))` virou `Amazon Echo / Alexa`, `Smart TV (GSD)` virou `Smart TV (GSD)` e `Celular / Tablet (Privacidade ativa) (Randomized/Mobile)` virou apenas `Celular / Tablet (Privacidade ativa)`).
- **Novo Comando `/confiar` (`/memorizar`) no Telegram:**
  - Permite cadastrar todos os dispositivos atualmente conectados na rede como seguros com um único comando ou toque, atualizando o banco de dados do Hub e a whitelist do nó.
- **Whitelist Residencial Homologada:**
  - Registrados e homologados os 10 dispositivos do ecossistema doméstico em Campina Grande (Roteador, PC, Vigia Moto E7 Plus, 2 Alexas, Smart TV, Soundbar, Samsung Galaxy e celulares pessoais com MAC aleatório).

## [1.3.0] - 2026-11-18

### Adicionado & Aperfeiçoado
- **Detecção Inteligente de Localização Geográfica (Campina Grande vs João Pessoa):**
  - O sentinela agora identifica automaticamente onde está sem necessidade de alteração manual em arquivos de configuração:
    - Se conectado na rede de Campina Grande (SSID `brisa-1082152`, BSSID `24:2f:d0:76:41:b4` ou GeoIP de Campina Grande), assume automaticamente: `Casa Campina Grande (Quarto)`.
    - Ao ser transportado para o apartamento em João Pessoa / Cabedelo, identifica a nova rede e assume automaticamente: `Apê João Pessoa (Ponta de Campina)`.
    - Todas as respostas de comandos, cartões fixados e relatórios utilizam o local real detectado.
- **Varredura Subnet Completa & Correção de Descoberta de Dispositivos:**
  - Corrigido o método de descoberta de rede: o ping em endereço broadcast (`.255`) vinha sendo descartado pela maioria dos dispositivos modernos (Windows, TVs, smartphones) por padrão de segurança (RFC 1122).
  - Implementada varredura paralela ultrarrápida (menos de 2 segundos) em todos os 254 IPs da sub-rede ativa.
  - Tabela ARP do kernel do Moto E7 Plus agora captura 100% dos dispositivos conectados (cabos ethernet, Wi-Fi 2.4/5GHz, Smart TVs, Soundbars, dispositivos Amazon Echo/Alexa, computadores e celulares).
- **Correção de Colisão no Parser do Bot do Telegram:**
  - O botão `🛡️ Dispositivos na Rede` continha o termo `rede`, que coincidia anteriormente com a rota do comando de Wi-Fi/Internet antes de chegar ao Radar. A precedência foi corrigida para priorizar comandos específicos.
- **Categorização Inteligente de Dispositivos na Rede (`/radar` / `🛡️ Dispositivos na Rede`):**
  - **Duas Seções Claras e Estruturadas:**
    1. `🟢 Dispositivos Conectados Atualmente`: Exibe todos os aparelhos ativos usando a rede Wi-Fi/cabo no imóvel no momento.
    2. `🟡 Dispositivos Novos / Não Cadastrados`: Destaca claramente se há algum aparelho desconhecido ou não presente na whitelist, emitindo alerta visual com IP e MAC; caso todos sejam conhecidos, confirma "Todos os aparelhos conectados são conhecidos e confiáveis".
  - **Classificador Heurístico Inteligente de Hardware (`classify_device`):**
    - Identifica automaticamente e rotula por categoria amigável e ícone funcional:
      - 🌐 **Roteador / Repetidor de Wi-Fi** (Gateway / Intelbras / ZTE / TP-Link / Huawei)
      - 📺 **Smart TV & Streaming** (Samsung TV / LG webOS / Roku / Fire TV / Chromecast)
      - 📱 **Smartphone / Tablet** (Apple iPhone / Samsung Galaxy / Motorola / Xiaomi)
      - 📱 **Dispositivo Móvel com Privacidade Ativa** (Detecção heurística de MAC aleatório de celulares modernos com segundo nibble `2, 6, a, e`)
      - 💡 **Casa Inteligente & IoT** (Chips Espressif ESP8266/ESP32, Sonoff, Tuya, Philips Hue)
      - 💻 **Computador / Notebook** (Dell, Lenovo, HP, Intel, Realtek, Microsoft)
      - 🛡️ **Celular Vigia (Moto E7 Plus)** (Identificação determinística do próprio aparelho)
  - **Contrato de Telemetria e Banco SQLite:**
    - Modelo `LanDevice` incorporado a `HeartbeatPayload`.
    - Tabela SQLite WAL `lan_devices` com upsert automático a cada ciclo de telemetria.
- **Inteligência Preditiva em Todas as Demais Pontas do Projeto:**
  - **⚡ Geladeira & Luz Inteligente (`/geladeira`):**
    - Estimativa da curva de retenção térmica de eletrodomésticos fechados (4h–6h na geladeira; 24h–36h no congelador).
    - Contagem regressiva em tempo real com orientações em caso de falta de luz ("Mantenha a geladeira fechada!").
    - Estimativa de autonomia de bateria do celular vigia para continuar alertando o usuário.
    - Índice de Estabilidade Elétrica das últimas 24h (0–100).
  - **🔋 Bateria & Hardware Inteligente (`/bateria`):**
    - Contagem de desgaste evitado em ciclos de recarga pelo circuito PMI632 bypass.
    - Avaliação de segurança por tensão de célula (< 4.2V vs zona perigosa de estufamento de 4.4V).
    - Estimativa indireta da temperatura ambiente do cômodo do apê a partir dos sensores em repouso.
  - **🌐 Conexão de Internet Inteligente (`/internet`):**
    - Score de estabilidade da operadora de internet (0–100).
    - Diagnóstico de adequação para Streaming 4K, Chamadas de Vídeo / WhatsApp e Latência de jogos.
    - Explicação didática da independência entre rede e energia.
  - **📊 Relatório 24 Horas Inteligente (`/relatorio`):**
    - Índice de Estabilidade do Apê unificado (0–100).
    - Resumo executivo em narrativa humana simples e direta para qualquer membro da família.
  - **⚡ Status do Apê (`/status`):**
    - Diagnóstico de 3 segundos em destaque visual no topo da resposta.
- **Testes & Validação:**
  - Suíte de testes automatizados expandida para 20 testes com 100% de sucesso.
  - Deploy completo testado e operacional no Moto E7 Plus e no Hub Nobara Linux.

## [1.2.0] - 2026-11-18

### Adicionado & Aperfeiçoado
- **Bot Interativo Bidirecional no Telegram:**
  - Registrado o menu oficial de comandos no Telegram via `setMyCommands` (botão azul "Menu" ao lado da caixa de texto).
  - Adicionado teclado interativo persistente (`ReplyKeyboardMarkup`) com botões grandes de toque (`⚡ Status do Apê`, `❄️ Geladeira & Luz`, `📊 Relatório 24h`, `🔋 Bateria`, etc.), eliminando 100% da necessidade de digitar para qualquer membro da família.
  - Implementado `TelegramBotWorker` em Python stdlib puro (`hub_linux/netsentinel_hub/telegram_bot.py`), escutando comandos em tempo real via long-polling com blindagem de segurança para responder apenas ao chat do proprietário.
  - Comandos suportados:
    - `/status`: Retorna na hora o estado ao vivo da tomada, porcentagem e modo da bateria, sinal Wi-Fi e tempo de atividade.
    - `/geladeira` (ou `/energia`): Diagnóstico focado em segurança alimentar, estado da tomada e balanço de quedas nas últimas 24h.
    - `/bateria`: Diagnóstico detalhado de hardware, temperatura, tensão e modo bypass do PMIC Qualcomm PMI632.
    - `/internet` (ou `/rede`): Qualidade do sinal Wi-Fi e estabilidade contra quedas de provedor.
    - `/radar` (ou `/seguranca`): Auditoria de dispositivos conectados na rede local (LAN).
    - `/relatorio`: Balanço executivo das últimas 24 horas (quedas, tempo sem luz, temperaturas e integridade).
    - `/historico`: Retorna as últimas quedas de energia/internet e eventos do prédio diretamente do banco SQLite WAL.
    - `/ping`: Teste de latência e sinal de vida da Central Nobara Linux.
    - `/alerta`: Dispara um teste rápido de notificação na tela e no Telegram.
    - `/ajuda`: Exibe o guia interativo de comandos categorizado.
- **Alerta Imediato de Tomada Sem Energia (`POWER_LOST`):**
  - Implementado tratamento em `telegram_client.sh` e `state_machine.py`. Se a tomada for desconectada enquanto a internet estiver ativa (ex.: disjuntor do cômodo desarmou ou cabo solto), o usuário recebe aviso imediato no Telegram sem precisar esperar a luz voltar.
- **Watchdog Autorregenerativo de Wi-Fi no Android:**
  - Se a tomada estiver ligada mas a internet falhar por 5 ciclos consecutivos (5 min) após uma oscilação do roteador, o nó executa `cmd wifi set-wifi-enabled enabled` para forçar o Android a se reconectar ao AP.
- **Backoff Adaptativo do Hub e Timeout Ultrarrápido:**
  - Reduzido o timeout de conexão do `curl` para `--connect-timeout 4 --max-time 8`.
  - Se o Hub estiver inacessível repetidamente (ex.: nó em João Pessoa e PC desligado em Campina Grande), o nó entra em backoff suave, tentando o Hub a cada 5 min em vez de queimar tempo/bateria todo minuto, operando 100% autônomo via Telegram.
- **Proteção de Memória Flash e Sensores:**
  - Blindagem em `battery_limiter.sh` contra leituras corrompidas de bateria (`BATT_PCT < 0`), mantendo bypass preventivo.
  - Rotação automática de `daemon.log` acima de 256 KB para preservar a vida útil da memória eMMC do Moto E7 Plus.
- **Conectividade Remota João Pessoa ↔ Campina Grande:**
  - Script [`scripts/setup_remote_tailscale.sh`](scripts/setup_remote_tailscale.sh) adicionado para conectar o PC ao Moto E7 Plus via rede mesh WireGuard criptografada e gratuita, permitindo que o Hub monitore o nó à distância sem abrir portas no roteador.
- **Qualidade & Testes:**
  - Nova suíte de testes `tests/test_telegram_bot.py` e validação de `POWER_LOST`. Total de 19 testes passando com 100% de sucesso.

## [1.1.0] - 2026-11-18

### Modificado & Melhorado
- **Humanização Integral do Telegram:**
  - Status Card fixado completamente redesenhado com indicador visual claro de tomada, porcentagem e temperatura da bateria, qualidade do sinal Wi-Fi traduzida (Excelente, Bom, Regular, Fraco), tempo ativo em dias/horas e data/hora local em horário de Brasília (`America/Fortaleza`).
  - Textos de alerta reescritos em linguagem direta e humana, substituindo termos técnicos obscuros (`RSSI`, `OUI`, `Anomalia de Espectro`) por avisos claros como "A luz voltou!", "A internet voltou!" e "Novo dispositivo conectado na rede do apê!".
- **Eliminação de Falso Alarme de Repetidores e Vizinhos:**
  - Adicionado parâmetro `SPECTRUM_SCAN_WIFI_APS=0` (padrão): desativa a busca de beacons Wi-Fi de vizinhos no ar, eliminando disparos causados por repetidores de tomada e redes vizinhas do condomínio. Mantém foco estrito na detecção de intrusão via rede local (LAN / `ip neigh`).
- **Resolução de Duplicações e Repetição em Fila (Anti-Spam):**
  - Implementada lógica hierárquica de despacho: quando o Hub está online (`hub_ok=1`), ele assume os alertas e o nó limpa as filas sem gerar mensagens duplicadas no Telegram.
  - Se o Hub estiver inacessível, o nó despacha diretamente para o Telegram e limpa as filas atômicas imediatamente após a confirmação HTTP 200, eliminando o reenvio repetitivo a cada minuto.
- **Resiliência Extrema a Reinicializações:**
  - **Android (Moto E7 Plus):** `99-netsentinel.sh` ganhou loop supervisor com auto-reinício em 5 segundos caso o processo seja interrompido, prevenção de instâncias duplicadas via `pgrep` e bypass permanente de Doze.
  - **Linux (Nobara):** Unit `netsentinel-hub.service` atualizada com `Restart=always` e `RestartSec=5` vinculada a `default.target` com `loginctl enable-linger yuri` ativo para início automático antes mesmo do login gráfico.
- **Automação no Deploy (`scripts/deploy_adb.sh`):**
  - Sincronização automática e segura do `.env` local para o `/data/adb/netsentinel/node.env` no aparelho via Python, com permissão `0600`, limpeza de filas antigas e reinício gracioso do daemon.
- **Compatibilidade POSIX:** Fallback automático de `SCRIPT_DIR` para `${NS_ROOT}` caso os scripts sejam executados ou carregados a partir de stdin/heredocs.

## [1.0.0] - 2026-11-18

### Adicionado
- **Fase 0 Empírica:** Script [`scripts/probe_e7plus.sh`](scripts/probe_e7plus.sh) e relatório [`ENVIRONMENT_REPORT.md`](ENVIRONMENT_REPORT.md) com validação real do PMIC Qualcomm PMI632 (`charging_enabled=0` em bypass real de `0 uA` e detecção de tomada via `usb/present`) no Moto E7 Plus com LineageOS 24.0 + Magisk v31.1.
- **Nó Android (`node_android/`):**
  - `lib_common.sh`: escrita atômica (`tmp + fsync + mv`), HMAC-SHA256 RFC 2104 em POSIX puro (`sha256sum + xxd + awk`), wakelocks parciais e lookup OUI offline.
  - `battery_limiter.sh`: controle de carga 60%–75% em modo bypass + descarga suave acima de 82% + corte térmico de emergência em 40.0 °C (histerese 37.0 °C).
  - `power_blackbox.sh`: caixa-preta atômica (`blackbox.log`) com diferenciação forense `REAL_POWER_OUTAGE` vs `ISP_ONLY_OUTAGE` e alerta de risco alimentar para geladeira.
  - `spectrum_auditor.sh`: varredura passiva Wi-Fi (`cmd wifi`/`iw`), LAN (`ip neigh`) e cache Bluetooth (`dumpsys bluetooth_manager`) com whitelist, `--learn-whitelist`, persistência RSSI e cooldown.
  - `telegram_client.sh`: envio direto de alertas críticos e Status Card fixado atualizado in-place via Telegram Bot API (redundância 24/7 mesmo com PC desligado).
  - `netsentinel_node.sh` + `service.d/99-netsentinel.sh`: daemon persistente com Modo Normal (60s) e Modo Sobrevivência (120s + tela desligada).
- **Hub Nobara Linux (`hub_linux/`):**
  - Servidor HTTP dual-stack IPv4/IPv6 em Python stdlib (`< 25 MB` RAM) com verificação HMAC-SHA256 em tempo constante e anti-replay (300s).
  - Máquina de estados Dead Man's Switch (180s), deduplicação de alertas, persistência SQLite WAL + snapshot JSON atômico, notificações KDE (`notify-send`) + Telegram, CLI `netsentinel-hub` e unit `systemd --user`.
- **Automação & Qualidade:** Scripts `scripts/deploy_adb.sh`, `scripts/setup_hub.sh` e suíte `pytest` completa em `tests/`.
