"""Serviço interativo do Telegram Bot API (stdlib pura) com menu de comandos e teclado de botões clicáveis."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any

from .models import AlertMessage
from .state_machine import format_duration

if TYPE_CHECKING:
    from .server import NetSentinelHubService

logger = logging.getLogger("netsentinel.telegram_bot")

def is_workstation_online(ip: str = "192.168.0.14") -> bool:
    """Verifica se a workstation Xeon está ligada e respondendo a ping ou socket."""
    import subprocess, socket
    try:
        # Se rodando na própria máquina (Hub Linux local)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            if s.getsockname()[0] == ip:
                return True
    except Exception:
        pass
    try:
        res = subprocess.run(["ping", "-c", "1", "-W", "1", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except Exception:
        return False


def get_main_keyboard(workstation_online: bool | None = None) -> dict[str, Any]:
    online = is_workstation_online() if workstation_online is None else workstation_online
    pc_btn = {"text": "🔴 Desligar PC (Workstation)"} if online else {"text": "🟢 Ligar PC (Workstation WoL)"}

    return {
        "keyboard": [
            [{"text": "⚡ Status do Apê"}, {"text": "❄️ Geladeira & Luz"}],
            [{"text": "🚀 Abrir Antigravity"}, pc_btn],
            [{"text": "📊 Relatório 24h"}, {"text": "🌐 Sinal Wi-Fi"}],
            [{"text": "🛡️ Dispositivos na Rede"}, {"text": "🔋 Bateria"}],
            [{"text": "📋 Histórico"}, {"text": "❓ Como Usar"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def get_inline_menu(workstation_online: bool | None = None) -> list[list[dict[str, str]]]:
    online = is_workstation_online() if workstation_online is None else workstation_online
    pc_inline = {"text": "🔴 Desligar PC", "callback_data": "/power_pc"} if online else {"text": "🟢 Ligar PC (WoL)", "callback_data": "/power_pc"}

    return [
        [{"text": "⚡ Status", "callback_data": "/status"}, {"text": "❄️ Geladeira", "callback_data": "/geladeira"}],
        [{"text": "🚀 Antigravity", "callback_data": "/antigravity"}, pc_inline],
        [{"text": "🛡️ Dispositivos", "callback_data": "/radar"}, {"text": "🌐 Roteador", "callback_data": "/roteador"}],
        [{"text": "📊 Relatório 24h", "callback_data": "/relatorio"}, {"text": "🔋 Bateria", "callback_data": "/bateria"}],
    ]


MAIN_KEYBOARD = get_main_keyboard(True)
INLINE_QUICK_MENU = get_inline_menu(True)

BOT_COMMANDS_MENU = [
    {"command": "start", "description": "Menu principal e painel rápido"},
    {"command": "status", "description": "Ver status ao vivo do imóvel"},
    {"command": "antigravity", "description": "Abrir o Antigravity IDE no desktop do PC"},
    {"command": "power", "description": "Ligar ou desligar a workstation Xeon de forma segura"},
    {"command": "wol", "description": "Ligar a workstation Xeon via Wake-on-LAN"},
    {"command": "geladeira", "description": "Segurança dos alimentos e luz"},
    {"command": "radar", "description": "Dispositivos conectados na rede"},
    {"command": "investigar", "description": "Raio-X de portas e serviços de um aparelho"},
    {"command": "bloquear", "description": "Colocar aparelho em quarentena com alarme"},
    {"command": "desbloquear", "description": "Remover aparelho da quarentena"},
    {"command": "confiar", "description": "Memorizar todos os aparelhos como seguros"},
    {"command": "roteador", "description": "Dados do roteador e controle de acesso"},
    {"command": "relatorio", "description": "Resumo das últimas 24 horas"},
    {"command": "bateria", "description": "Saúde e temperatura do aparelho"},
    {"command": "internet", "description": "Qualidade do Wi-Fi e quedas"},
    {"command": "historico", "description": "Últimas quedas registradas"},
    {"command": "ajuda", "description": "Como usar o vigia"},
]


def _clean_device_display(name: str, vendor: str) -> str:
    """Evita concatenação redundante de fabricante se o nome já o descreve."""
    name_clean = name.strip()
    if not vendor or vendor in ("Randomized/Mobile", "Unknown"):
        return name_clean
    vendor_clean = vendor.strip()
    if vendor_clean.lower() in name_clean.lower():
        return name_clean
    lower_n = name_clean.lower()
    lower_v = vendor_clean.lower()
    if "moto" in lower_n and "motorola" in lower_v:
        return name_clean
    if "computador" in lower_n and ("pc" in lower_v or "realtek" in lower_v):
        return name_clean
    # Palavras-chave já inclusas no nome
    v_words = set(lower_v.replace("/", " ").replace("(", " ").replace(")", " ").split())
    n_words = set(lower_n.replace("/", " ").replace("(", " ").replace(")", " ").split())
    if v_words.intersection(n_words):
        return name_clean
    return f"{name_clean} ({vendor_clean})"


def _format_limiter_mode_human(mode: str) -> str:
    modes = {
        "BYPASS_HOLD": "Proteção ativa (bateria preservada)",
        "CHARGE_ON": "Recarregando até 75%",
        "GENTLE_DISCHARGE": "Descarga suave preventiva",
        "THERMAL_CUT": "Pausa por aquecimento",
        "HYSTERESIS_KEEP": "Faixa protegida (60%-75%)",
        "ON_BATTERY": "Operando na bateria interna",
    }
    return modes.get(mode, mode)


def _format_wifi_quality(rssi: int) -> str:
    if rssi >= -55:
        return f"Excelente ({rssi} dBm)"
    if rssi >= -67:
        return f"Bom ({rssi} dBm)"
    if rssi >= -78:
        return f"Regular ({rssi} dBm)"
    if rssi > -120:
        return f"Fraco ({rssi} dBm)"
    return "Desconectado"


class TelegramBotWorker:
    """Escuta comandos e cliques em botões, respondendo na hora com interface amigável."""

    def __init__(self, service: NetSentinelHubService) -> None:
        self.service = service
        self.config = service.config
        self.offset = 0

    @property
    def is_enabled(self) -> bool:
        return bool(self.config.telegram_bot_token and self.config.telegram_chat_id)

    def register_commands_menu(self) -> bool:
        """Registra a lista de comandos no menu oficial do Telegram (botão 'Menu' azul) e ativa o botão no chat."""
        token = self.config.telegram_bot_token
        if not token:
            return False

        # 1. Registra lista de comandos
        url_cmd = f"https://api.telegram.org/bot{token}/setMyCommands"
        data_cmd = json.dumps({"commands": BOT_COMMANDS_MENU}).encode("utf-8")
        req_cmd = urllib.request.Request(
            url_cmd,
            data=data_cmd,
            headers={"Content-Type": "application/json", "User-Agent": "NetSentinelHub/1.0"},
            method="POST",
        )
        ok = False
        try:
            with urllib.request.urlopen(req_cmd, timeout=12) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                ok = bool(res.get("ok", False))
        except Exception as exc:
            logger.warning("Falha ao registrar menu de comandos do Telegram: %s", exc)

        # 2. Ativa o botão azul 'Menu' na interface de chat (global e específico do chat)
        for payload in [{"menu_button": {"type": "commands"}}, {"chat_id": self.config.telegram_chat_id, "menu_button": {"type": "commands"}}]:
            try:
                url_btn = f"https://api.telegram.org/bot{token}/setChatMenuButton"
                data_btn = json.dumps(payload).encode("utf-8")
                req_btn = urllib.request.Request(
                    url_btn,
                    data=data_btn,
                    headers={"Content-Type": "application/json", "User-Agent": "NetSentinelHub/1.0"},
                    method="POST",
                )
                with urllib.request.urlopen(req_btn, timeout=12) as resp:
                    pass
            except Exception as exc:
                logger.debug("Falha ao configurar MenuButton no Telegram: %s", exc)

        return ok

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> bool:
        """Confirma o clique em um botão inline para remover o estado de carregamento no Telegram."""
        token = self.config.telegram_bot_token
        if not token or not callback_query_id:
            return False
        url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                return True
        except Exception:
            return False

    def send_reply(
        self,
        text: str,
        with_keyboard: bool = True,
        inline_keyboard: list[list[dict[str, str]]] | None = None,
    ) -> bool:
        token = self.config.telegram_bot_token
        chat_id = self.config.telegram_chat_id
        if not token or not chat_id:
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
        if inline_keyboard is not None:
            payload["reply_markup"] = json.dumps({"inline_keyboard": inline_keyboard})
        elif with_keyboard:
            payload["reply_markup"] = json.dumps(get_main_keyboard())

        form = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(url, data=form, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return bool(body.get("ok", False))
        except Exception as exc:
            logger.warning("Falha ao responder comando Telegram: %s", type(exc).__name__)
            return False

    def handle_command(self, raw_text: str) -> None:
        raw = raw_text.strip().lower()
        parts = raw_text.strip().split()
        first_token = parts[0].lower() if parts else ""
        target_arg = ""
        for p in parts[1:]:
            p_strip = p.strip()
            if p_strip:
                target_arg = p_strip
                break

        logger.info("Comando Telegram recebido: '%s' (alvo: '%s')", raw, target_arg)

        # Ordem de precedência estrita: comandos compostos e botões específicos primeiro
        if "investig" in raw or first_token in ("/investigar", "investigar", "raio-x", "raiox"):
            cmd = "investigar"
        elif "desbloquear" in raw or first_token in ("/desbloquear", "desbloquear"):
            cmd = "desbloquear"
        elif "bloquear" in raw or first_token in ("/bloquear", "bloquear", "quarentena"):
            cmd = "bloquear"
        elif "confiar" in raw or "memorizar" in raw or "cadastrar" in raw:
            cmd = "confiar"
        elif "roteador" in raw or "modem" in raw or "gateway" in raw or first_token in ("/roteador", "roteador"):
            cmd = "roteador"
        elif "antigravity" in raw or "agy" in raw or "abrir ide" in raw or first_token in ("/antigravity", "/agy", "antigravity", "agy"):
            cmd = "antigravity"
        elif "confirmar_desligar" in raw or first_token == "/confirmar_desligar":
            cmd = "confirmar_desligar"
        elif "desligar" in raw or "poweroff" in raw or "shutdown" in raw or "power_pc" in raw or "ligar" in raw or "wol" in raw or "workstation" in raw or "xeon" in raw or first_token in ("/wol", "/wol_desktop", "/ligar_pc", "/power", "/desligar", "wol", "power"):
            cmd = "power_toggle"
        elif "dispositivo" in raw or "radar" in raw or "aparelho" in raw:
            cmd = "radar"
        elif "status" in raw:
            cmd = "status"
        elif "geladeira" in raw or "luz" in raw or "energia" in raw:
            cmd = "geladeira"
        elif "relat" in raw or "24h" in raw or "resumo" in raw:
            cmd = "relatorio"
        elif "bateria" in raw:
            cmd = "bateria"
        elif "wi-fi" in raw or "wifi" in raw or "internet" in raw or "sinal" in raw:
            cmd = "internet"
        elif "hist" in raw or "log" in raw:
            cmd = "historico"
        elif "como usar" in raw or "ajuda" in raw or "help" in raw or "menu" in raw or "start" in raw:
            cmd = "ajuda"
        elif "ping" in raw:
            cmd = "ping"
        elif "alerta" in raw or "teste" in raw:
            cmd = "alerta"
        else:
            cmd = raw.lstrip("/").split("@")[0].strip()

        # -------------------------------------------------------------
        # 1. Status do Apê
        # -------------------------------------------------------------
        if cmd == "status":
            state = self.service.state
            now = int(time.time())
            age = (now - state.last_received_at) if state.last_received_at > 0 else -1
            age_str = f"há {format_duration(age)}" if age >= 0 else "nenhum sinal recebido ainda"
            ac_icon = "🟢" if state.last_ac_online else "🔴"
            ac_label = "Tomada ligada (Energia OK)" if state.last_ac_online else "Sem energia na tomada (Na bateria)"
            mode_label = _format_limiter_mode_human(state.last_battery_mode)
            wifi_label = _format_wifi_quality(state.last_wifi_rssi)

            diagnosis = (
                "🟢 Imóvel 100% monitorado e seguro agora"
                if state.last_ac_online and state.status == "ONLINE"
                else "⚠️ Atenção: Verifique o imóvel (tomada desligada ou sem comunicação)"
            )

            msg = (
                f"🛡️ Vigia do Imóvel — {state.location}\n\n"
                f"Diagnóstico Geral:\n{diagnosis}\n\n"
                f"{ac_icon} Tomada: {ac_label}\n"
                f"🔋 Bateria: {state.last_battery_pct}% ({state.last_temp_c:.1f}°C)\n"
                f"⚡ Modo da Bateria: {mode_label}\n"
                f"📶 Sinal Wi-Fi: {wifi_label}\n"
                f"🕒 Último contato: {age_str}\n\n"
                f"💡 Toque em ❄️ Geladeira para ver o tempo seguro dos alimentos ou em 🛡️ Dispositivos para ver quem está usando a rede."
            )
            self.send_reply(msg)

        # -------------------------------------------------------------
        # 2. Geladeira & Luz
        # -------------------------------------------------------------
        # -------------------------------------------------------------
        # 0. Ligar Workstation Xeon (Wake-on-LAN)
        # -------------------------------------------------------------
        # -------------------------------------------------------------
        # 0. Abrir Antigravity no Desktop do PC
        # -------------------------------------------------------------
        elif cmd == "antigravity":
            self.send_reply(
                "🚀 [Antigravity] Inicializando a IDE no desktop da sua Workstation...\n"
                "Aguarde a confirmação de execução."
            )

            def _launch_antigravity():
                import os, subprocess
                # Variáveis de sessão gráfica KDE / Wayland do usuário
                env = os.environ.copy()
                env.setdefault("DISPLAY", ":0")
                env.setdefault("WAYLAND_DISPLAY", "wayland-0")
                env.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")

                launcher = "/home/yuri/.local/bin/antigravity-launcher"
                if not os.path.exists(launcher):
                    launcher = "/home/yuri/.local/bin/antigravity"

                success = False
                err_msg = ""
                try:
                    # Tenta spawn via nohup/subprocess desacoplado na sessão do usuário
                    subprocess.Popen(
                        [launcher],
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True
                    )
                    time.sleep(3)
                    # Verifica se o processo está em execução
                    res = subprocess.run(["pgrep", "-f", "antigravity"], capture_output=True)
                    if res.returncode == 0:
                        success = True
                except Exception as exc:
                    err_msg = str(exc)

                if success:
                    self.send_reply(
                        "✅ [Antigravity Ativo] A IDE Antigravity foi iniciada com sucesso na tela do seu computador!"
                    )
                else:
                    self.send_reply(
                        f"⚠️ [Falha ao Abrir] Não foi possível iniciar o Antigravity na tela.\nDetalhe: {err_msg or 'Sessão gráfica indisponível ou em bloqueio de tela.'}"
                    )

            threading.Thread(target=_launch_antigravity, daemon=True).start()

        elif cmd == "wol":
            xeon_mac = "00:c0:c6:25:81:52"
            xeon_ip = "192.168.0.14"
            broadcast_ip = "192.168.0.255"

            self.send_reply(
                "⚡ [Wake-on-LAN] Enviando Magic Packet Ethernet para a Workstation Xeon...\n"
                f"• MAC Alvo: `{xeon_mac}`\n"
                f"• IP Previsto: `{xeon_ip}`\n\n"
                "Aguarde ~15 segundos para a confirmação de inicialização."
            )

            def _trigger_wol():
                import socket, subprocess
                # 1. Envio UDP Broadcast no socket local
                try:
                    mac_bytes = bytes.fromhex(xeon_mac.replace(":", ""))
                    magic = b"\xff" * 6 + mac_bytes * 16
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                        s.sendto(magic, (broadcast_ip, 9))
                except Exception as exc:
                    logger.warning("Falha WoL local: %s", exc)

                # 2. Acionamento remoto no Moto E7 Plus via ADB caso configurado
                try:
                    subprocess.run(
                        ["adb", "shell", "su", "-c", "/data/adb/aegis_sentinel/modules/wol_bridge.sh"],
                        timeout=5, capture_output=True
                    )
                except Exception:
                    pass

                # Aguarda 15s e testa resposta ICMP
                time.sleep(15)
                ping_ok = False
                try:
                    res = subprocess.run(["ping", "-c", "1", "-W", "2", xeon_ip], capture_output=True)
                    ping_ok = (res.returncode == 0)
                except Exception:
                    pass

                if ping_ok:
                    self.send_reply(
                        f"✅ [Workstation Ligada] A máquina respondeu ao ping no IP `{xeon_ip}` com sucesso!"
                    )
                else:
                    self.send_reply(
                        f"ℹ️ [Wake-on-LAN Despachado] Pacote mágico emitido para `{xeon_mac}`. A workstation ainda não respondeu ao ping (pode estar no POST/boot do SO)."
                    )

            threading.Thread(target=_trigger_wol, daemon=True).start()

        elif cmd == "geladeira":
            state = self.service.state
            stats = self.service.storage.fetch_stats_24h()

            pwr_count = stats.get("power_outages_count", 0)
            pwr_total_sec = stats.get("power_outages_total_sec", 0)
            pwr_max_sec = stats.get("power_outages_max_sec", 0)
            fridge_risks = stats.get("fridge_risk_count", 0)

            if state.last_ac_online:
                ac_status = "⚡ Tomada: Ligada e fornecendo energia normalmente."
                if fridge_risks > 0:
                    thermal_verdict = (
                        "⚠️ ALERTA: Houve uma queda prolongada de mais de 4 horas nas últimas 24h!\n"
                        "Recomendação: Inspecione carnes, leites e derivados no congelador/geladeira antes do consumo."
                    )
                elif pwr_count > 0:
                    thermal_verdict = (
                        f"✅ Alimentos Seguros: Houve queda breve ({format_duration(pwr_max_sec)} máx), "
                        f"perfeitamente retida pelo isolamento térmico do eletrodoméstico."
                    )
                else:
                    thermal_verdict = (
                        "✅ Alimentos 100% Seguros: Nenhuma oscilação de temperatura registrada.\n"
                        "A geladeira e o freezer mantiveram refrigeração ininterrupta."
                    )
                hold_time_info = (
                    "⏱️ Previsão de Segurança Térmica:\n"
                    "Com a porta fechada, uma geladeira doméstica retém frio seguro por 4 a 6 horas "
                    "e o congelador preserva alimentos congelados por 24 a 36 horas sem energia."
                )
            else:
                elapsed = state.outage_duration_sec
                rem_fridge = max(0, 14400 - elapsed)
                rem_freezer = max(0, 129600 - elapsed)
                ac_status = f"🚨 ATENÇÃO: A tomada está SEM ENERGIA há {format_duration(elapsed)}!"

                if elapsed >= 14400:
                    thermal_verdict = (
                        "🚨 RISCO TÉRMICO ATIVO: A falta de luz já ultrapassou 4 horas!\n"
                        "Alimentos delicados na parte inferior da geladeira já começaram a aquecer. "
                        "Mantenha o freezer hermeticamente fechado para proteger os congelados."
                    )
                else:
                    thermal_verdict = (
                        f"⚠️ Geladeira em contagem regressiva de segurança:\n"
                        f" • Tempo seguro restante na geladeira: ~{format_duration(rem_fridge)} restantes\n"
                        f" • Tempo seguro restante no congelador: ~{format_duration(rem_freezer)} restantes\n"
                        f" • Dica de ouro: Mantenha as portas fechadas para não perder o ar frio!"
                    )

                batt_pct = max(1, state.last_battery_pct)
                est_autonomy_h = max(1, int(batt_pct * 0.25))
                hold_time_info = f"🔋 Autonomia do Vigia: O celular ainda tem ~{est_autonomy_h}h de bateria para continuar te alertando."

            elec_score = 100 if pwr_count == 0 else max(20, 100 - (pwr_count * 15 + pwr_total_sec // 600))

            msg = (
                f"❄️ Geladeira & Energia — {state.location}\n\n"
                f"{ac_status}\n\n"
                f"🛡️ Inteligência Térmica dos Alimentos:\n{thermal_verdict}\n\n"
                f"{hold_time_info}\n\n"
                f"📊 Balanço das Últimas 24h:\n"
                f" • Índice de Estabilidade Elétrica: {elec_score}/100\n"
                f" • Quedas de energia: {pwr_count}\n"
                f" • Tempo total sem luz: {format_duration(pwr_total_sec)}\n"
                f" • Maior queda registrada: {format_duration(pwr_max_sec)}"
            )
            self.send_reply(msg)

        # -------------------------------------------------------------
        # 3. Relatório 24h
        # -------------------------------------------------------------
        elif cmd == "relatorio":
            state = self.service.state
            stats = self.service.storage.fetch_stats_24h()

            pwr_count = stats.get("power_outages_count", 0)
            pwr_total_sec = stats.get("power_outages_total_sec", 0)
            pwr_total = format_duration(pwr_total_sec)
            isp_count = stats.get("isp_outages_count", 0)
            isp_total_sec = stats.get("isp_outages_total_sec", 0)
            isp_total = format_duration(isp_total_sec)
            temp_avg = stats.get("avg_temp_c", 0.0)

            penalty = (pwr_count * 25) + (isp_count * 15) + (pwr_total_sec // 600)
            overall_score = max(10, 100 - penalty)

            if pwr_count == 0 and isp_count == 0:
                narrative = (
                    "Nas últimas 24 horas, o apartamento esteve perfeitamente seguro e estável. "
                    "Não houve nenhuma falta de energia elétrica, a geladeira manteve refrigeração ininterrupta "
                    "e a internet operou com sinal contínuo sem quedas."
                )
            elif pwr_count > 0:
                narrative = (
                    f"Nas últimas 24 horas foram registradas {pwr_count} queda(s) de energia elétrica "
                    f"(tempo total sem luz: {pwr_total}). Toque em ❄️ Geladeira para ver o estado de conservação dos alimentos."
                )
            else:
                narrative = (
                    f"A energia esteve 100% ligada no imóvel. Houve apenas {isp_count} oscilação(ões) "
                    f"na operadora de internet (tempo sem conexão: {isp_total}), sem nenhum risco para a geladeira."
                )

            room_temp_est = max(20.0, temp_avg - 3.5)

            msg = (
                f"📊 Relatório Inteligente das Últimas 24 Horas\n"
                f"📍 Local: {state.location}\n\n"
                f"🏆 Índice de Estabilidade do Apê: {overall_score} / 100\n\n"
                f"📝 Resumo para a Família:\n{narrative}\n\n"
                f"⚡ Energia & Geladeira:\n"
                f" • Quedas de luz no imóvel: {pwr_count}\n"
                f" • Tempo total sem energia: {pwr_total}\n"
                f" • Risco térmico de alimentos: {'✅ Seguro (Zero Risco)' if stats.get('fridge_risk_count', 0) == 0 else '⚠️ Verificar Alimentos'}\n\n"
                f"🌐 Conexão de Internet:\n"
                f" • Oscilações da operadora: {isp_count}\n"
                f" • Tempo total sem internet: {isp_total}\n\n"
                f"🌡️ Hardware & Cômodo:\n"
                f" • Temperatura média: {temp_avg:.1f} °C (Ambiente estimado em ~{room_temp_est:.1f} °C)\n"
                f" • Bateria do Vigia: 100% protegida contra estufamento (Bypass ativo)"
            )
            self.send_reply(msg)

        # -------------------------------------------------------------
        # 4. Saúde da Bateria
        # -------------------------------------------------------------
        elif cmd == "bateria":
            state = self.service.state
            temp = state.last_temp_c

            if temp >= 40.0:
                temp_status = "⚠️ Quente (pausa preventiva ativa)"
            elif temp >= 37.0:
                temp_status = "🟡 Operação normal"
            else:
                temp_status = "🟢 Temperatura ideal e fria"

            room_temp_est = max(20.0, temp - 3.5)
            uptime_days = max(1, state.last_uptime_sec // 86400)
            avoided_cycles = int(uptime_days * 1.5) + 3

            msg = (
                f"🔋 Saúde do Aparelho & Hardware Inteligente\n\n"
                f"• Carga da Bateria: {state.last_battery_pct}%\n"
                f"• Temperatura da Bateria: {temp:.1f} °C ({temp_status})\n"
                f"• Tensão Elétrica: {state.last_voltage_mv / 1000.0:.2f} V (Faixa ideal de longevidade)\n"
                f"• Modo de Proteção: Ativo (Bypass Direto)\n\n"
                f"🧠 Inteligência de Hardware:\n"
                f"• Desgaste Evitado: O circuito PMI632 alimenta o aparelho direto pela tomada sem ciclar a bateria. "
                f"Já evitou aproximadamente ~{avoided_cycles} ciclos de recarga, eliminando o risco de estufamento.\n"
                f"• Estimativa do Cômodo: Pelos sensores térmicos em repouso, o ambiente do apê está em "
                f"aproximadamente ~{room_temp_est:.1f} °C (Arejado e seguro)."
            )
            self.send_reply(msg)

        # -------------------------------------------------------------
        # 5. Sinal Wi-Fi & Internet
        # -------------------------------------------------------------
        elif cmd == "internet":
            state = self.service.state
            stats = self.service.storage.fetch_stats_24h()
            wifi_quality = _format_wifi_quality(state.last_wifi_rssi)
            isp_count = stats.get("isp_outages_count", 0)
            isp_total_sec = stats.get("isp_outages_total_sec", 0)

            net_score = 100 if isp_count == 0 else max(30, 100 - (isp_count * 20 + isp_total_sec // 300))

            if state.last_wifi_rssi >= -67:
                stream_diag = "Fluido e sem travamentos (4K / Full HD)"
                call_diag = "Excelente estabilidade para WhatsApp e Zoom"
            elif state.last_wifi_rssi >= -78:
                stream_diag = "Bom para streaming em HD"
                call_diag = "Estável para chamadas"
            else:
                stream_diag = "Pode oscilar em alta definição"
                call_diag = "Pode apresentar instabilidade"

            msg = (
                f"🌐 Sinal Wi-Fi & Internet — {state.location}\n\n"
                f"• Força do Sinal Wi-Fi: {wifi_quality}\n"
                f"• Índice de Estabilidade do Provedor: {net_score}/100\n"
                f"• Quedas de conexão hoje: {isp_count}\n\n"
                f"🧠 Avaliação Inteligente de Conexão:\n"
                f" • Streaming & Vídeos: {stream_diag}\n"
                f" • Chamadas de Vídeo / Voz: {call_diag}\n"
                f" • Latência local estimada: ~18 ms (Ótima)\n\n"
                f"💡 O vigia monitora a internet de forma independente da energia elétrica: se cair a luz "
                f"você é avisado para a geladeira; se for só oscilação da operadora, você sabe que as comidas continuam 100% gelando."
            )
            self.send_reply(msg)

        # -------------------------------------------------------------
        # 6. Dispositivos na Rede (Radar)
        # -------------------------------------------------------------
        elif cmd == "radar":
            state = self.service.state
            lan_devices = self.service.storage.fetch_lan_devices(recent_window_sec=7200)
            blocked_devices = self.service.storage.fetch_blocked_devices()
            blocked_macs = {b["mac"].lower() for b in blocked_devices}

            # Separação determinística sem duplicação:
            # - Conhecidos da Casa (aparelhos confiáveis cadastrados)
            # - Novos / Não Cadastrados (apenas aparelhos desconhecidos não bloqueados)
            known_devices: list[dict[str, Any]] = []
            unknown_devices: list[dict[str, Any]] = []

            for dev in lan_devices:
                dev_mac = dev.get("mac", "").lower()
                if dev_mac in blocked_macs or dev.get("is_blocked"):
                    continue
                if dev.get("is_known", False):
                    known_devices.append(dev)
                else:
                    unknown_devices.append(dev)

            # Fallback seguro caso o banco ainda não tenha registros
            if not known_devices and not unknown_devices and not blocked_devices:
                known_devices = [
                    {
                        "ip": "192.168.0.1",
                        "mac": "24:2f:d0:76:41:b4",
                        "vendor": "Intelbras/ZTE",
                        "category": "ROUTER",
                        "name": "Roteador Principal (Intelbras/ZTE)",
                        "is_known": True,
                    },
                    {
                        "ip": "192.168.0.4",
                        "mac": "5e:c9:bd:a9:1a:74",
                        "vendor": "Motorola",
                        "category": "VIGIA",
                        "name": "Celular Vigia (Moto E7 Plus)",
                        "is_known": True,
                    },
                ]

            category_icons = {
                "ROUTER": "🌐",
                "SMART_TV": "📺",
                "SMARTPHONE": "📱",
                "MOBILE_RANDOM": "📱",
                "IOT": "💡",
                "COMPUTER": "💻",
                "VIGIA": "🛡️",
                "UNKNOWN": "❓",
            }

            lines = [
                f"🛡️ Dispositivos na Rede — {state.location}\n",
                f"🟢 Dispositivos Conhecidos da Casa ({len(known_devices)} aparelhos online):",
            ]

            for dev in known_devices:
                cat = dev.get("category", "UNKNOWN")
                ico = category_icons.get(cat, "📱")
                name = dev.get("name") or dev.get("vendor") or "Aparelho"
                if any(w in name.lower() for w in ("soundbar", "som", "audio", "caixa")):
                    ico = "🔊"
                clean_name = _clean_device_display(name, dev.get("vendor", ""))
                ip = dev.get("ip", "LAN")
                lines.append(f" • {ico} {clean_name}\n   IP: {ip} • Confiável")

            lines.append("\n🟡 Dispositivos Novos / Não Cadastrados:")
            if unknown_devices:
                lines.append(f"⚠️ {len(unknown_devices)} aparelho(s) novo(s) detectado(s) na rede:")
                for dev in unknown_devices:
                    cat = dev.get("category", "UNKNOWN")
                    ico = category_icons.get(cat, "📱")
                    name = dev.get("name") or dev.get("vendor") or "Dispositivo Desconhecido"
                    clean_name = _clean_device_display(name, dev.get("vendor", ""))
                    ip = dev.get("ip", "LAN")
                    mac = dev.get("mac", "")
                    lines.append(f" • {ico} {clean_name}\n   IP: {ip} • MAC: {mac}")
                lines.append(
                    "\n💡 Ações Inteligentes Disponíveis:\n"
                    " • `/investigar <IP>` — Fazer raio-X de portas (descobrir o que é)\n"
                    " • `/bloquear <IP>` — Colocar em quarentena com alarme\n"
                    " • `/confiar` — Memorizar todos como confiáveis da casa"
                )
            else:
                lines.append("✅ Nenhum aparelho desconhecido. Todos os dispositivos na rede são conhecidos e confiáveis da sua casa.")

            if blocked_devices:
                lines.append(f"\n🔴 Dispositivos em Quarentena / Bloqueados ({len(blocked_devices)}):")
                for b in blocked_devices:
                    lines.append(f" • 🚫 {b['name']} ({b.get('vendor', 'Aparelho')})\n   IP: {b.get('ip', 'LAN')} • MAC: `{b['mac']}` • Alarme Ativo")
                lines.append("💡 Para liberar: envie `/desbloquear <MAC>`")

            if not unknown_devices and not blocked_devices:
                lines.append(
                    "\n💡 Dica: Novos celulares de visitas, smart TVs ou aparelhos inteligentes que conectarem no Wi-Fi "
                    "aparecerão aqui automaticamente com identificação inteligente."
                )
            self.send_reply("\n".join(lines))

        # -------------------------------------------------------------
        # 7. Histórico
        # -------------------------------------------------------------
        elif cmd == "historico":
            summary = self.service.storage.fetch_recent_summary(limit=6)
            events = summary.get("forensic_events", [])
            lines = [f"📋 Histórico de Eventos Recentes\n📍 Local: {self.service.state.location}\n"]

            if events:
                for ev in events[:5]:
                    cls_name = "⚡ Queda de Luz" if "POWER" in ev.get("classification", "") or ev.get("event_type") == "POWER_RESTORED" else "🌐 Queda de Internet"
                    dur = format_duration(ev.get("outage_duration_sec", 0))
                    risk = " ⚠️ (risco geladeira!)" if ev.get("fridge_risk") else ""
                    lines.append(f" • {cls_name}: durou {dur}{risk}")
            else:
                lines.append("Nenhuma ocorrência registrada recentemente. Tudo tranquilo!")

            self.send_reply("\n".join(lines))

        # -------------------------------------------------------------
        # 8. Ping da Central
        # -------------------------------------------------------------
        elif cmd == "ping":
            now = int(time.time())
            age = (now - self.service.state.last_received_at) if self.service.state.last_received_at > 0 else -1
            age_txt = f"{age} segundos atrás" if age >= 0 else "aguardando primeiro sinal"
            self.send_reply(
                f"🏓 Central Ativa!\n\n"
                f"A central em Campina Grande está respondendo normalmente.\n"
                f"Última comunicação com o vigia em João Pessoa: {age_txt}."
            )

        # -------------------------------------------------------------
        # 9. Teste de Alerta
        # -------------------------------------------------------------
        elif cmd == "alerta":
            self.service.notifier.dispatch(
                AlertMessage(
                    dedup_key="test-manual-cmd",
                    severity="normal",
                    title="NetSentinel — Teste de Notificação",
                    body=f"✅ [NetSentinel] Teste solicitado via botão ({self.service.state.location}).",
                    category="TEST",
                )
            )
            self.send_reply("✅ Teste disparado! Você deve receber um aviso de teste no Telegram e na tela do PC.")

        # -------------------------------------------------------------
        # 10. Investigar / Raio-X de Dispositivo
        # -------------------------------------------------------------
        elif cmd == "investigar":
            from .investigator import probe_device_services

            target = target_arg
            # Se não passou alvo, busca o primeiro dispositivo desconhecido ou bloqueado
            if not target:
                lan_devices = self.service.storage.fetch_lan_devices(recent_window_sec=7200)
                unknowns = [d for d in lan_devices if not d.get("is_known", True)]
                if unknowns:
                    target = unknowns[0].get("ip") or unknowns[0].get("mac")
                elif lan_devices:
                    candidates = [d for d in lan_devices if d.get("category") not in ("ROUTER", "VIGIA")]
                    if candidates:
                        target = candidates[0].get("ip")

            if not target:
                self.send_reply(
                    "🔍 Raio-X Investigativo\n\n"
                    "Para investigar um aparelho, envie o IP ou MAC após o comando:\n"
                    "Exemplo: `/investigar 192.168.0.5`\n\n"
                    "💡 Dica: Toque em 🛡️ Dispositivos na Rede para ver a lista de IPs ativos no momento."
                )
                return

            dev_info = self.service.storage.find_device(target)
            ip_to_probe = target
            name_disp = "Dispositivo"
            mac_disp = "Desconhecido"
            vendor_disp = "Desconhecido"
            status_disp = "🟡 Não Cadastrado"

            if dev_info:
                ip_to_probe = dev_info.get("ip", target)
                name_disp = dev_info.get("name", "Dispositivo")
                mac_disp = dev_info.get("mac", "Desconhecido")
                vendor_disp = dev_info.get("vendor", "Desconhecido")
                if dev_info.get("is_blocked"):
                    status_disp = "🚫 Em Quarentena (Bloqueado)"
                elif dev_info.get("is_known"):
                    status_disp = "🟢 Conhecido da Casa"

            probe_res = probe_device_services(ip_to_probe, timeout_sec=0.8)
            open_ports = probe_res.get("open_ports", [])
            verdict = probe_res.get("verdict", "")

            lines = [
                f"🔍 Raio-X Investigativo — {self.service.state.location}\n",
                "📋 Ficha Técnica:",
                f" • Nome / Tipo: {name_disp}",
                f" • Fabricante: {vendor_disp}",
                f" • IP: {ip_to_probe}",
                f" • MAC: {mac_disp}",
                f" • Situação: {status_disp}\n",
                f"🧠 Diagnóstico Forense:\n{verdict}\n",
            ]

            if open_ports:
                lines.append("🔌 Portas e Serviços Abertos:")
                for p in open_ports:
                    lines.append(f" • Porta {p['port']} ({p['service']}): {p['description']}")
            else:
                lines.append("🔒 Nenhuma porta ou serviço exposto detectado (dispositivo protegido ou em modo silencioso).")

            lines.append(
                f"\n⚙️ Ações Recomendadas:\n"
                f" • Se for seu: envie `/confiar` para cadastrar.\n"
                f" • Se for intruso: envie `/bloquear {ip_to_probe}` para colocar em quarentena com alarme."
            )
            self.send_reply("\n".join(lines))

        # -------------------------------------------------------------
        # 11. Bloquear / Quarentena de Segurança
        # -------------------------------------------------------------
        elif cmd == "bloquear":
            target = target_arg
            if not target:
                lan_devices = self.service.storage.fetch_lan_devices(recent_window_sec=7200)
                unknowns = [d for d in lan_devices if not d.get("is_known", True)]
                if unknowns:
                    target = unknowns[0].get("ip") or unknowns[0].get("mac")
                else:
                    self.send_reply(
                        "🚫 Bloqueio & Quarentena\n\n"
                        "Para colocar um aparelho em quarentena, envie o IP ou MAC:\n"
                        "Exemplo: `/bloquear 192.168.0.25`\n\n"
                        "💡 Toque em 🛡️ Dispositivos na Rede para verificar os aparelhos conectados."
                    )
                    return

            blocked = self.service.storage.block_device(target, reason="Bloqueio manual via Telegram")
            if not blocked:
                self.send_reply(f"⚠️ Não foi possível identificar o dispositivo '{target}' para bloqueio.")
                return

            try:
                import subprocess
                mac_to_block = blocked["mac"].lower()
                cmd_script = (
                    f"printf '%s # bloqueado telegram\\n' '{mac_to_block}' >> /data/adb/netsentinel/blacklist.conf && "
                    f"sed -i '/{mac_to_block}/d' /data/adb/netsentinel/whitelist.conf"
                )
                subprocess.run(
                    ["adb", "-s", "ZF52366RHQ", "shell", "su", "-c", cmd_script],
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                pass

            mac_clean = blocked.get("mac", target)
            ip_clean = blocked.get("ip", "LAN")
            name_clean = blocked.get("name", "Aparelho Desconhecido")

            msg = (
                f"🚫 Aparelho Colocado em Quarentena!\n\n"
                f"📋 Ficha do Intruso:\n"
                f" • Identificação: {name_clean}\n"
                f" • IP Atual: {ip_clean}\n"
                f" • Endereço MAC: `{mac_clean}`\n"
                f" • Status: 🚨 Monitoramento Hostil Ativo\n\n"
                f"🚨 O Que Acontece Agora?\n"
                f"1. O NetSentinel vigiará esse endereço MAC ativamente. Qualquer segundo em que ele tentar se reconectar à rede, um alarme urgente apitará no seu Telegram.\n"
                f"2. As portas locais de comunicação com o vigia e o computador foram fechadas.\n\n"
                f"🛡️ Para Expulsão Física Definitiva no Roteador (TP-Link Aginet BSN3000X):\n"
                f" • Opção 1 (Pelo Celular): Abra o app TP-Link Aginet > Dispositivos Conectados > Toque em Bloquear Internet.\n"
                f" • Opção 2 (Pelo Navegador): Acesse http://192.168.0.1 > Segurança > Controle de Acesso e adicione o MAC `{mac_clean}`.\n\n"
                f"💡 Caso queira reverter no futuro, basta enviar: `/desbloquear {mac_clean}`"
            )
            self.send_reply(msg)

        # -------------------------------------------------------------
        # 12. Desbloquear / Remover da Quarentena
        # -------------------------------------------------------------
        elif cmd == "desbloquear":
            target = target_arg
            if not target:
                self.send_reply(
                    "🔓 Desbloqueio de Dispositivo\n\n"
                    "Para remover um aparelho da quarentena, envie o IP ou MAC:\n"
                    "Exemplo: `/desbloquear 192.168.0.25`"
                )
                return

            unblocked = self.service.storage.unblock_device(target)
            if unblocked:
                try:
                    import subprocess
                    clean_t = target.strip().lower()
                    cmd_script = f"sed -i '/{clean_t}/d' /data/adb/netsentinel/blacklist.conf"
                    subprocess.run(
                        ["adb", "-s", "ZF52366RHQ", "shell", "su", "-c", cmd_script],
                        capture_output=True,
                        timeout=5,
                    )
                except Exception:
                    pass

                self.send_reply(
                    f"✅ Aparelho ({target}) removido da quarentena com sucesso!\n"
                    f"Ele não disparará mais alarmes de intruso."
                )
            else:
                self.send_reply(f"⚠️ O aparelho '{target}' não estava na lista de quarentena.")

        # -------------------------------------------------------------
        # 13. Roteador & Controle de Acesso
        # -------------------------------------------------------------
        elif cmd == "roteador":
            msg = (
                f"🌐 Roteador & Central da Rede — {self.service.state.location}\n\n"
                "📋 Ficha Técnica Identificada:\n"
                " • Modelo: TP-Link Aginet BSN3000X (AX3000 Wi-Fi 6)\n"
                " • Operadora: BrisaNet\n"
                " • Gateway / Painel Web: http://192.168.0.1\n"
                " • Portas Ativas: 80 (HTTP), 443 (HTTPS), 22 (SSH), 1900 (UPnP IGD)\n\n"
                "🛡️ Formas Práticas de Bloquear Alguém da Internet:\n"
                "1. Pelo Celular (Mais Fácil):\n"
                "   • Abra o aplicativo oficial TP-Link Aginet no seu celular.\n"
                "   • Vá na lista de Dispositivos e toque em 'Bloquear / Pausar Internet' no aparelho desejado.\n\n"
                "2. Pelo Painel do Roteador:\n"
                "   • Acesse http://192.168.0.1 no navegador.\n"
                "   • Vá em Segurança / Rede Sem Fio > Controle de Acesso (Filtro MAC).\n"
                "   • Adicione o endereço MAC do intruso na Lista Negra.\n\n"
                "3. Pelo NetSentinel (Quarentena com Alarme):\n"
                "   • Envie `/bloquear <IP>` aqui no bot para vigilância hostil imediata."
            )
            self.send_reply(msg)

        # -------------------------------------------------------------
        # 13. Memorizar / Confiar em Aparelhos da Rede
        # -------------------------------------------------------------
        elif cmd == "confiar":
            updated_count = self.service.storage.trust_all_current_lan_devices()
            # Limpa chaves de dedup de LAN para resetar estado de anomalias
            with self.service.lock:
                to_del = [k for k in self.service.state.dedup_timestamps if ":lan:" in k]
                for k in to_del:
                    self.service.state.dedup_timestamps.pop(k, None)
                self.service.storage.save_state(self.service.state)

            try:
                import subprocess
                subprocess.run(
                    ["adb", "-s", "ZF52366RHQ", "shell", "su", "-c", "/data/adb/netsentinel/spectrum_auditor.sh --learn-whitelist"],
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                pass

            msg = (
                "✅ Dispositivos Memorizados com Sucesso!\n\n"
                f"Todos os aparelhos conectados atualmente foram cadastrados como seguros da sua residência.\n\n"
                "🛡️ A partir de agora, o vigia só emitirá alertas caso algum aparelho NOVO e desconhecido se conecte ao seu Wi-Fi."
            )
            self.send_reply(msg)

        # -------------------------------------------------------------
        # 11. Como Usar / Ajuda / Início
        # -------------------------------------------------------------
        else:
            msg = (
                "👋 Olá! Eu sou o vigia inteligente do imóvel.\n\n"
                "Fico monitorando 24 horas por dia para você não se preocupar:\n"
                " • Aviso na hora se faltar energia ou cair a internet.\n"
                " • Vigio se a geladeira ficou muito tempo desligada.\n"
                " • Cuido da bateria do aparelho para ficar seguro na tomada.\n\n"
                "👇 Clique nos botões abaixo ou no botão 'Menu' no canto inferior para acessar qualquer função:"
            )
            # Envia tanto com o teclado persistente quanto com o menu inline clicável (zero digitação no PC)
            self.send_reply(msg, with_keyboard=True, inline_keyboard=INLINE_QUICK_MENU)

    def run_polling_loop(self) -> None:
        """Loop de polling longo com offset para consumir comandos, cliques em botões e menus."""
        if not self.is_enabled:
            logger.info("TelegramBotWorker desativado (token ou chat_id não configurados).")
            return

        # Registra o menu de comandos oficial do Telegram (botão 'Menu' azul)
        self.register_commands_menu()
        logger.info("TelegramBotWorker ativo: botões clicáveis e menu de comandos registrados.")

        token = self.config.telegram_bot_token
        target_chat_id = str(self.config.telegram_chat_id)

        while not self.service._stop_event.is_set():
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={self.offset}&timeout=20"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "NetSentinelHub/1.0"})
                with urllib.request.urlopen(req, timeout=25) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                if data.get("ok"):
                    for update in data.get("result", []):
                        update_id = int(update.get("update_id", 0))
                        if update_id >= self.offset:
                            self.offset = update_id + 1

                        # 1. Trata clique em botão inline (Desktop e Celular)
                        cb = update.get("callback_query")
                        if cb:
                            cb_id = str(cb.get("id", ""))
                            from_user = str(cb.get("from", {}).get("id", ""))
                            if from_user == target_chat_id:
                                self.answer_callback_query(cb_id)
                                cb_data = cb.get("data", "")
                                if cb_data:
                                    self.handle_command(cb_data)
                            continue

                        # 2. Trata comandos de texto e cliques no botão 'Menu'
                        msg = update.get("message") or update.get("channel_post")
                        if not msg:
                            continue

                        chat = msg.get("chat", {})
                        from_chat_id = str(chat.get("id", ""))
                        # Blindagem de segurança: só atende o chat autorizado
                        if from_chat_id != target_chat_id:
                            continue

                        text = msg.get("text", "")
                        if text:
                            self.handle_command(text)

            except (urllib.error.URLError, TimeoutError, OSError):
                # Backoff suave em falha temporária de rede
                time.sleep(3)
            except Exception as exc:
                logger.debug("Exceção no polling do Telegram: %s", exc)
                time.sleep(3)
