# NetSentinel — Relatório Empírico de Ambiente (Fase 0)

**Data da sondagem final (com root):** 2026-11-18 13:42 (-03:00)  
**Script reprodutível:** [`scripts/probe_e7plus.sh`](scripts/probe_e7plus.sh)

---

## 1. Central de Controle (Host Nobara Linux)

| Item | Resultado Empírico |
| :--- | :--- |
| **Sistema Operacional** | Nobara Linux 44 (KDE Plasma Desktop Edition) |
| **Kernel** | `7.3.1-201.nobara.fc44.x86_64` |
| **Sessão Gráfica / D-Bus** | Wayland (`WAYLAND_DISPLAY=wayland-0`), `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus` |
| **Python & Stdlib** | `Python 3.14.8` (`/usr/bin/python3`), `sqlite3` nativo `3.51.2`, `urllib`, `hmac`, `hashlib`, `ssl` |
| **Gerenciadores / Compiladores** | `uv` (`/home/yuri/.local/bin/uv`), `pip3`, `rustc 1.94.0` + `cargo`, `gcc` |
| **Ferramentas Instaladas** | `adb`, `fastboot`, `notify-send 0.8.9`, `systemctl --user` (`running`), `curl`, `jq`, `openssl` |
| **Ferramentas Ausentes** | `pytest` global (gerenciado em `.venv` via `uv`), `wg`, `tailscale`, `sqlite3` CLI, `go` |
| **Firewall Local** | `firewalld` ativo (`running`) |

### Topologia de Rede do Host
- **Interface ativa (`enp7s0`):**
  - IPv4 local: `192.168.0.14/24` (gateway `192.168.0.1`)
  - IPv4 público: `177.37.144.225` (NAT do roteador / potencial CGNAT)
  - IPv6 global: `2804:29b8:505a:5d50:4964:89aa:bb96:c613/64` (idêntico ao IPv6 externo)
- **Decisão Arquitetural:**
  - **Canal Primário Zero-Custo (PC Desligado / CGNAT-Proof):** O nó no Moto E7 Plus envia alertas críticos e atualiza um *Status Card* fixado diretamente na **Telegram Bot API** via HTTPS de saída (`curl 8.16.0-DEV` com BoringSSL validado).
  - **Canal Hub (Dead Man's Switch + Auditoria Local):** O `netsentinel-hub` em Python stdlib no Nobara recebe heartbeats assinados (HMAC-SHA256) por HTTP sobre LAN, IPv6 global ou Tailscale Free (opcional) e dispara alertas via `notify-send -u critical` + Telegram ao completar 180s sem batimento.

---

## 2. Nó Sentinela Remoto (Motorola Moto E7 Plus — Serial `ZF52366RHQ`)

### Evolução do Ambiente na Fase 0
Na primeira sondagem, o aparelho encontrava-se na Stock ROM Motorola Android 10 sem root (bootloader já desbloqueado). Para viabilizar acesso integral ao `sysfs` de energia, wakelocks, `service.d` e eliminar o overhead do Google Play Services, foi realizado o flash oficial de:
- **ROM:** **LineageOS 24.0** (`24.0-20261112-NIGHTLY-guam`, Android 17 / SDK 37, sem Google Play Services)
- **Kernel:** `4.19.325-cip136-st20-perf-g1da2d0861244` (`aarch64`)
- **Root:** **Magisk v31.1** (`31.1:MAGISKSU`, `uid=0(root) context=u:r:magisk:s0`, preinit `/metadata/watchdog/magisk`)
- **Persistência de Boot:** `/data/adb/service.d/` e `/data/adb/post-fs-data.d/` validados e graváveis (`drwxr-xr-x root:root`)
- **Memória / ZRAM:** `MemTotal: 3816604 kB`, `MemAvailable: 1767212 kB`, `/dev/block/zram0` = 2.67 GB ativo
- **Fuso Horário:** `GMT` (UTC) — todos os timestamps do sistema utilizam epoch UTC (`date -u +%s`)

---

## 3. Validação Empírica de Energia e PMIC (`qcom,qpnp-smb5` / PMI632)

### Nós Confirmados em `/sys/class/power_supply/`
- `battery` -> `...pmi632@2:qcom,qpnp-smb5/power_supply/battery`
- `bms` -> `...pmi632@2:qpnp,qg/power_supply/bms` (`battery_type=JK50-ATL-FLEX-4850MAH`, `soh=54`, `cycle_count=42`, `charge_full=4025000 uAh`)
- `main` -> `...pmi632@2:qcom,qpnp-smb5/power_supply/main`
- `pc_port` -> `...pmi632@2:qcom,qpnp-smb5/power_supply/pc_port`
- `usb` -> `...pmi632@2:qcom,qpnp-smb5/power_supply/usb`

### Teste Real de Controle de Carga (60%–75%) e Detecção de Queda Elétrica

| Estado Testado | `battery/status` | `charging_enabled` | `input_suspend` | `battery/current_now` | `usb/present` | `usb/voltage_now` |
| :--- | :--- | :---: | :---: | ---: | :---: | ---: |
| **Baseline (carregando)** | `Charging` | `1` | `0` | `+144195 uA` | `1` | `4862272 uV` |
| **Teste A (`charging_enabled=0`)** | `Not charging` | `0` | `0` | **`0 uA` (Bypass Real!)** | **`1`** | **`5040816 uV`** |
| **Teste B (`input_suspend=1`)** | `Not charging` | `1` | `1` | `-171661 uA` (drena bateria) | `1` | `5097920 uV` |
| **Restaurado (`charging_enabled=1`)** | `Charging` | `1` | `0` | `+164184 uA` | `1` | `4917296 uV` |

### Descobertas Críticas de Engenharia
1. **Modo Bypass Real (`charging_enabled=0`):** Ao gravar `0` em `/sys/class/power_supply/battery/charging_enabled`, o PMI632 corta a corrente da bateria para exatos **`0 uA`** e alimenta o aparelho diretamente pela fonte externa — eliminando micro-ciclos de carga/descarga e aquecimento em operação 24/7.
2. **Desacoplamento Total Entre Limite de Carga e Queda de Energia:** Mesmo com `charging_enabled=0` (ou `input_suspend=1`), `/sys/class/power_supply/usb/present` permanece `1` e `/sys/class/power_supply/usb/voltage_now` permanece em ~5.0 V (`> 3500000 uV`). Quando falta luz na tomada, `usb/present` cai para `0`, `usb/online` e `pc_port/online` vão a `0` e `usb/voltage_now` cai abaixo de 2.0 V. Logo, **suspender a carga em 75% jamais gera falso positivo de queda de energia**.
3. **Armadilha Evitada (`usb/online` em porta USB SDP):** Em porta USB de computador, `usb/online` marca `0` enquanto `pc_port/online=1` e `usb/present=1`. Por isso, o sensor primário de alimentação externa é `usb/present == 1` (confirmado por `usb/online == 1 || pc_port/online == 1 || usb/voltage_now >= 3500000`).
4. **Temperatura:** `/sys/class/power_supply/battery/temp` opera em **décimos de °C** (ex.: `335` = `33.5 °C`). Corte térmico de emergência configurado em `>= 400` (`40.0 °C`) com histerese de retorno em `<= 370` (`37.0 °C`).

---

## 4. Binários, Rede, Espectro e Wakelocks Validados sob Root

| Capacidade | Resultado Empírico |
| :--- | :--- |
| **Shell & Coreutils** | `/system/bin/sh`, `/system_ext/bin/bash`, `toybox 0.8.13-android`, `/data/adb/magisk/busybox` (`v1.36.1.1`) |
| **HTTP / TLS** | `/system/bin/curl 8.16.0-DEV` (BoringSSL, HTTPS/IPv6/MQTT/WS) — `https://api.telegram.org` respondeu `HTTP/1.1 302` |
| **Banco & Cripto** | `/system/bin/sqlite3 3.50.6`, `sha256sum`, `xxd`, `od`, `base64`, `awk`, `sed`, `flock`, `fsync`, `timeout`, `setsid` (sem `python3` e sem `openssl`) |
| **Varredura Wi-Fi** | `cmd wifi start-scan` + `cmd wifi list-scan-results` (retorna BSSID, Frequência, RSSI, Idade e SSID em ~1s sem derrubar `wlan0`); `iw wlan0 link` e `iw wlan0 scan dump` também operacionais |
| **Varredura LAN** | `ip neigh show` operacional (IPv4/IPv6 + MAC + estado `REACHABLE`/`STALE`) |
| **Bluetooth / BLE** | Sem `/dev/hci*` ou BlueZ (`btmgmt`/`hcitool` ausentes); controlador gerenciado pela pilha Android HAL/Binder. `cmd bluetooth_manager` expõe apenas controle de estado (`enable`/`enableBle`), sem CLI de scan BLE ativo. O auditor de espectro integra `dumpsys bluetooth_manager` para dispositivos em cache/pareados além de Wi-Fi + LAN |
| **Wakelocks** | `/sys/power/wake_lock` e `/sys/power/wake_unlock` graváveis (`WAKELOCK_ACQUIRE=OK`, `WAKELOCK_RELEASE=OK`) |

### Decisão Final de Stack do Nó Android
O agente `netsentinel-node` será escrito em **POSIX Shell puro (`/system/bin/sh`)** apoiado em `toybox`, `curl 8.16.0-DEV`, `sqlite3`, `cmd wifi`, `iw` e `sha256sum` + `xxd` (HMAC-SHA256 RFC 2104 nativo sem depender de OpenSSL ou Python). Consumo de RAM em repouso: ~1–2 MB.
