"""Testes de integração ponta-a-ponta do NetSentinel Hub (HTTP, HMAC, SQLite WAL e CLI)."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from netsentinel_hub.cli import main as cli_main
from netsentinel_hub.config import HubConfig
from netsentinel_hub.models import AlertMessage
from netsentinel_hub.notifier import Notifier
from netsentinel_hub.security import compute_hmac_sha256
from netsentinel_hub.server import NetSentinelHubService, create_http_server


class _RecordingNotifier(Notifier):
    def __init__(self, config: HubConfig) -> None:
        super().__init__(config)
        self.sent: list[AlertMessage] = []

    def dispatch(self, alert: AlertMessage) -> dict[str, bool]:
        self.sent.append(alert)
        return {"desktop": True, "telegram": True}


def _make_config(tmp_path: Path, secret: str = "test-secret-0123456789abcdef") -> HubConfig:
    return HubConfig(
        node_id="e7plus-jp-ap",
        location_label="Apê João Pessoa",
        hmac_secret=secret,
        telegram_bot_token="",
        telegram_chat_id="",
        bind_host="127.0.0.1",
        bind_port=0,
        heartbeat_timeout_sec=180,
        watchdog_poll_sec=10,
        replay_window_sec=300,
        alert_cooldown_sec=600,
        fridge_risk_outage_sec=14400,
        data_dir=tmp_path / "hub_data",
        enable_desktop_notify=False,
    )


def test_http_heartbeat_ingestion_security_and_persistence(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    notifier = _RecordingNotifier(cfg)
    service = NetSentinelHubService(cfg, notifier=notifier)
    httpd = create_http_server(service)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        base_url = f"http://127.0.0.1:{port}"
        now_ts = int(time.time())
        payload_dict = {
            "node_id": "e7plus-jp-ap",
            "location": "Apê João Pessoa",
            "timestamp": now_ts,
            "ac_online": True,
            "battery_pct": 73,
            "temp_c": 32.4,
            "voltage_mv": 4012,
            "uptime_sec": 7200,
            "wifi_rssi": -55,
            "battery_mode": "BYPASS_HOLD",
            "pending_blackbox_events": [
                {
                    "event": "ISP_RESTORED",
                    "classification": "ISP_ONLY_OUTAGE",
                    "net_lost_ts": now_ts - 600,
                    "net_restored_ts": now_ts,
                    "outage_duration_sec": 600,
                    "ac_stayed_online": True,
                    "battery_pct": 73,
                }
            ],
            "spectrum_anomalies": [
                {
                    "ts": now_ts,
                    "source": "wifi",
                    "mac": "00:1c:b3:aa:bb:cc",
                    "vendor": "Apple",
                    "rssi_max": -49,
                    "rssi_now": -51,
                    "persist_sec": 240,
                    "label": "iPhone Intruso",
                }
            ],
        }
        raw = json.dumps(payload_dict).encode("utf-8")
        sig = f"sha256={compute_hmac_sha256(cfg.hmac_secret, raw)}"

        # 1. Requisição com assinatura inválida -> HTTP 401
        bad_req = urllib.request.Request(
            f"{base_url}/api/v1/heartbeat",
            data=raw,
            headers={"Content-Type": "application/json", "X-NetSentinel-Signature": "sha256=" + "0" * 64},
            method="POST",
        )
        try:
            urllib.request.urlopen(bad_req, timeout=5)
            raise AssertionError("Esperava HTTP 401 para assinatura inválida")
        except urllib.error.HTTPError as err:
            assert err.code == 401

        # 2. Requisição válida assinada -> HTTP 200 + 2 alertas (ISP_FORENSIC + SPECTRUM)
        good_req = urllib.request.Request(
            f"{base_url}/api/v1/heartbeat",
            data=raw,
            headers={"Content-Type": "application/json", "X-NetSentinel-Signature": sig},
            method="POST",
        )
        with urllib.request.urlopen(good_req, timeout=5) as resp:
            assert resp.status == 200
            body = json.loads(resp.read().decode("utf-8"))
            assert body["ok"] is True
            assert body["alerts_emitted"] == 2

        assert [a.category for a in notifier.sent] == ["ISP_FORENSIC", "SPECTRUM"]

        # 2.1. Teste de detecção de intruso bloqueado
        service.storage.block_device("a4:83:e7:11:22:33")
        hb_intruder = {
            "node_id": "e7plus-jp-ap",
            "location": "Apê João Pessoa",
            "timestamp": now_ts + 10,
            "ac_online": True,
            "battery_pct": 73,
            "temp_c": 32.4,
            "voltage_mv": 4012,
            "uptime_sec": 7210,
            "wifi_rssi": -55,
            "battery_mode": "BYPASS_HOLD",
            "lan_devices": [
                {
                    "ip": "192.168.0.88",
                    "mac": "a4:83:e7:11:22:33",
                    "vendor": "Apple",
                    "category": "SMARTPHONE",
                    "name": "Aparelho Intruso",
                    "is_known": False,
                }
            ],
        }
        raw_int = json.dumps(hb_intruder).encode("utf-8")
        sig_int = compute_hmac_sha256(cfg.hmac_secret, raw_int)
        req_int = urllib.request.Request(
            f"{base_url}/api/v1/heartbeat",
            data=raw_int,
            headers={"Content-Type": "application/json", "X-NetSentinel-Signature": sig_int},
            method="POST",
        )
        with urllib.request.urlopen(req_int, timeout=5) as resp_int:
            assert resp_int.status == 200
            b_int = json.loads(resp_int.read().decode("utf-8"))
            assert b_int["alerts_emitted"] == 1
        assert notifier.sent[-1].category == "INTRUDER"
        assert "INTRUSO DETECTADO NA REDE" in notifier.sent[-1].body

        # 3. Healthcheck + persistência SQLite WAL + snapshot JSON atômico
        with urllib.request.urlopen(f"{base_url}/health", timeout=5) as hresp:
            hdata = json.loads(hresp.read().decode("utf-8"))
            assert hdata["state"]["status"] == "ONLINE"
            assert hdata["state"]["last_battery_pct"] == 73

        summary = service.storage.fetch_recent_summary(limit=5)
        assert len(summary["heartbeats"]) == 2
        assert len(summary["forensic_events"]) == 1
        assert len(summary["spectrum_anomalies"]) == 1
        assert len(summary["alerts"]) == 3
        assert (cfg.data_dir / "runtime_state.json").is_file()

        # 4. Watchdog dispara Dead Man's Switch após 180s sem novo heartbeat
        wd_alerts = service.run_watchdog_tick(now_ts=now_ts + 200)
        assert len(wd_alerts) == 1
        assert wd_alerts[0].category == "DEAD_MAN_SWITCH"
    finally:
        service.stop()
        httpd.shutdown()
        httpd.server_close()


def test_cli_status_audit_and_verify_env(tmp_path: Path, capsys) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"NETSENTINEL_HMAC_SECRET=abcdef1234567890\n"
        f"TELEGRAM_BOT_TOKEN=123:abc\n"
        f"TELEGRAM_CHAT_ID=999\n"
        f"HUB_DATA_DIR={tmp_path / 'data'}\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    assert cli_main(["--env", str(env_file), "verify-env"]) == 0
    assert "[OK]" in capsys.readouterr().out

    assert cli_main(["--env", str(env_file), "status", "--json"]) == 0
    status_out = json.loads(capsys.readouterr().out)
    assert status_out["status"] == "INIT"

    assert cli_main(["--env", str(env_file), "audit", "--limit", "5"]) == 0
    audit_out = json.loads(capsys.readouterr().out)
    assert audit_out["heartbeats"] == []
