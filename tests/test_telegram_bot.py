"""Testes do serviço interativo TelegramBotWorker."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from netsentinel_hub.config import HubConfig
from netsentinel_hub.models import HeartbeatPayload
from netsentinel_hub.server import NetSentinelHubService
from netsentinel_hub.telegram_bot import TelegramBotWorker


def _make_service(tmp_path: Path) -> NetSentinelHubService:
    cfg = HubConfig(
        node_id="e7plus-jp-ap",
        location_label="Apê João Pessoa",
        hmac_secret="0" * 32,
        telegram_bot_token="test_token",
        telegram_chat_id="12345",
        bind_host="127.0.0.1",
        bind_port=0,
        heartbeat_timeout_sec=180,
        watchdog_poll_sec=10,
        replay_window_sec=300,
        alert_cooldown_sec=600,
        fridge_risk_outage_sec=14400,
        data_dir=tmp_path / "data",
        enable_desktop_notify=False,
    )
    svc = NetSentinelHubService(cfg)
    hb = HeartbeatPayload.from_dict(
        {
            "node_id": "e7plus-jp-ap",
            "location": "Apê João Pessoa",
            "timestamp": 1000,
            "ac_online": True,
            "battery_pct": 74,
            "temp_c": 31.5,
            "voltage_mv": 4020,
            "uptime_sec": 7200,
            "wifi_rssi": -60,
            "battery_mode": "BYPASS_HOLD",
        }
    )
    svc.sm.process_heartbeat(svc.state, hb, received_at=1000)
    return svc


def test_telegram_bot_worker_commands(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    bot = TelegramBotWorker(svc)
    replies: list[str] = []
    bot.send_reply = MagicMock(side_effect=lambda text, *args, **kwargs: replies.append(text) or True)  # type: ignore[method-assign]

    # /status
    bot.handle_command("/status")
    assert len(replies) == 1
    assert "Vigia do" in replies[0]
    assert "74%" in replies[0]
    assert "Tomada ligada" in replies[0]

    # /geladeira
    bot.handle_command("/geladeira")
    assert len(replies) == 2
    assert "Geladeira" in replies[1]
    assert "Inteligência Térmica dos Alimentos" in replies[1]

    # /bateria
    bot.handle_command("/bateria")
    assert len(replies) == 3
    assert "Saúde do Aparelho" in replies[2]
    assert "Inteligência de Hardware" in replies[2]

    # /internet
    bot.handle_command("/internet")
    assert len(replies) == 4
    assert "Sinal Wi-Fi" in replies[3]
    assert "Avaliação Inteligente de Conexão" in replies[3]

    # /radar (duas seções limpas sem duplicação: Conhecidos da Casa + Novos/Não Cadastrados)
    bot.handle_command("/radar")
    assert len(replies) == 5
    assert "Dispositivos na Rede" in replies[4]
    assert "Dispositivos Conhecidos da Casa" in replies[4]
    assert "Dispositivos Novos / Não Cadastrados" in replies[4]

    # /relatorio
    bot.handle_command("/relatorio")
    assert len(replies) == 6
    assert "Relatório Inteligente das Últimas 24 Horas" in replies[5]
    assert "Índice de Estabilidade do Apê" in replies[5]

    # /ping
    bot.handle_command("/ping")
    assert len(replies) == 7
    assert "Central Ativa" in replies[6]

    # /ajuda
    bot.handle_command("/ajuda")
    assert len(replies) == 8
    assert "vigia inteligente do imóvel" in replies[7]

    # /historico
    bot.handle_command("/historico")
    assert len(replies) == 9
    assert "Histórico" in replies[8]

    # /confiar
    bot.handle_command("/confiar")
    assert len(replies) == 10
    assert "Dispositivos Memorizados com Sucesso" in replies[9]

    # /investigar (sem argumentos)
    bot.handle_command("/investigar")
    assert len(replies) == 11
    assert "Raio-X Investigativo" in replies[10]

    # /bloquear
    bot.handle_command("/bloquear 192.168.0.1")
    assert len(replies) == 12
    assert "Quarentena" in replies[11]

    # /desbloquear
    bot.handle_command("/desbloquear 192.168.0.1")
    assert len(replies) == 13
    assert "removido da quarentena" in replies[12]

    # /roteador
    bot.handle_command("/roteador")
    assert len(replies) == 14
    assert "TP-Link Aginet BSN3000X" in replies[13]


def test_telegram_bot_radar_with_lan_devices(tmp_path: Path) -> None:
    import time
    svc = _make_service(tmp_path)
    now = int(time.time())
    # Simula heartbeat com dispositivos identificados na LAN
    hb = HeartbeatPayload.from_dict(
        {
            "node_id": "e7plus-jp-ap",
            "location": "Apê João Pessoa",
            "timestamp": now,
            "ac_online": True,
            "battery_pct": 74,
            "temp_c": 31.5,
            "lan_devices": [
                {
                    "ip": "192.168.0.1",
                    "mac": "24:2f:d0:76:41:b4",
                    "vendor": "Intelbras/ZTE",
                    "category": "ROUTER",
                    "name": "Roteador Principal (Intelbras/ZTE)",
                    "is_known": True,
                },
                {
                    "ip": "192.168.0.15",
                    "mac": "00:1c:62:aa:bb:cc",
                    "vendor": "LG Electronics",
                    "category": "SMART_TV",
                    "name": "Smart TV LG (webOS)",
                    "is_known": True,
                },
                {
                    "ip": "192.168.0.88",
                    "mac": "a4:83:e7:11:22:33",
                    "vendor": "Apple",
                    "category": "SMARTPHONE",
                    "name": "iPhone de Visita",
                    "is_known": False,
                },
            ],
        }
    )
    svc.storage.record_heartbeat(hb, received_at=now)

    bot = TelegramBotWorker(svc)
    replies: list[str] = []
    bot.send_reply = MagicMock(side_effect=lambda text: replies.append(text) or True)  # type: ignore[method-assign]

    bot.handle_command("radar")
    assert len(replies) == 1
    resp = replies[0]
    # Seção 1: Conhecidos da casa
    assert "Dispositivos Conhecidos da Casa (2 aparelhos online)" in resp
    assert "Roteador Principal" in resp
    assert "Smart TV LG" in resp
    # Dispositivo desconhecido NÃO deve estar na Seção 1
    assert "• 📱 iPhone de Visita\n   IP: 192.168.0.88 • Confiável" not in resp

    # Seção 2: Dispositivo novo/não cadastrado destacado
    assert "Dispositivos Novos / Não Cadastrados" in resp
    assert "1 aparelho(s) novo(s) detectado(s)" in resp
    assert "a4:83:e7:11:22:33" in resp

    # Agora confia em todos os aparelhos
    bot.handle_command("/confiar")
    assert len(replies) == 2
    assert "Dispositivos Memorizados com Sucesso" in replies[1]

    # Novo radar: agora todos os 3 são conhecidos, e a Seção 2 informa rede segura
    bot.handle_command("radar")
    assert len(replies) == 3
    resp2 = replies[2]
    assert "Dispositivos Conhecidos da Casa (3 aparelhos online)" in resp2
    assert "Nenhum aparelho desconhecido" in resp2

    # Testa /investigar no iPhone
    bot.handle_command("/investigar 192.168.0.88")
    assert len(replies) == 4
    assert "Raio-X Investigativo" in replies[3]
    assert "192.168.0.88" in replies[3]

    # Testa /bloquear no iPhone -> deve entrar em quarentena
    bot.handle_command("/bloquear 192.168.0.88")
    assert len(replies) == 5
    assert "Quarentena" in replies[4]
    assert "a4:83:e7:11:22:33" in replies[4]

    # No radar seguinte, o aparelho bloqueado deve aparecer em seção de Quarentena
    bot.handle_command("radar")
    assert len(replies) == 6
    resp3 = replies[5]
    assert "Dispositivos em Quarentena / Bloqueados" in resp3
    assert "a4:83:e7:11:22:33" in resp3

    # Testa /desbloquear
    bot.handle_command("/desbloquear a4:83:e7:11:22:33")
    assert len(replies) == 7
    assert "removido da quarentena" in replies[6]


def test_telegram_bot_disabled_when_missing_config(tmp_path: Path) -> None:
    cfg = HubConfig(
        node_id="e7plus-jp-ap",
        location_label="Apê João Pessoa",
        hmac_secret="0" * 32,
        telegram_bot_token="",
        telegram_chat_id="",
        bind_host="127.0.0.1",
        bind_port=0,
        heartbeat_timeout_sec=180,
        watchdog_poll_sec=10,
        replay_window_sec=300,
        alert_cooldown_sec=600,
        fridge_risk_outage_sec=14400,
        data_dir=tmp_path / "data",
        enable_desktop_notify=False,
    )
    svc = NetSentinelHubService(cfg)
    bot = TelegramBotWorker(svc)
    assert not bot.is_enabled


def test_telegram_bot_commands_menu_and_callback() -> None:
    from netsentinel_hub.telegram_bot import BOT_COMMANDS_MENU, INLINE_QUICK_MENU
    assert BOT_COMMANDS_MENU[0]["command"] == "start"
    assert any(cmd["command"] == "status" for cmd in BOT_COMMANDS_MENU)
    assert any(cmd["command"] == "radar" for cmd in BOT_COMMANDS_MENU)
    assert len(INLINE_QUICK_MENU) >= 4
