"""Testes da máquina de estados de queda elétrica vs queda de ISP, Dead Man's Switch e deduplicação."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from netsentinel_hub.models import HeartbeatPayload
from netsentinel_hub.state_machine import NodeRuntimeState, SentinelStateMachine

REPO_ROOT = Path(__file__).resolve().parents[1]
BLACKBOX_SCRIPT = REPO_ROOT / "node_android" / "netsentinel" / "power_blackbox.sh"


def _bb_cmd(root_dir: Path, *args: str) -> str:
    env = os.environ.copy()
    env["NS_ROOT"] = str(root_dir)
    env["NS_STATE_DIR"] = str(root_dir / "state")
    env["NS_LOG_DIR"] = str(root_dir / "logs")
    res = subprocess.run(
        ["/bin/sh", str(BLACKBOX_SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def test_android_blackbox_real_power_outage_with_fridge_risk(tmp_path: Path) -> None:
    root = tmp_path / "ns"
    # Baseline normal
    assert _bb_cmd(root, "--step", "1000", "1", "1", "74", "4020", "32.0") == "NONE"

    # Energia + Wi-Fi caem juntos
    assert _bb_cmd(root, "--step", "1060", "0", "0", "74", "3980", "31.5") == "POWER_LOST"

    # Durante a queda prolongada, bateria cai para 18% -> evento LOW_BATT_OUTAGE uma única vez
    assert _bb_cmd(root, "--step", "15000", "0", "0", "18", "3610", "29.0") == "LOW_BATT_OUTAGE"
    assert _bb_cmd(root, "--step", "15120", "0", "0", "17", "3600", "29.0") == "NONE"

    # Energia e Wi-Fi retornam após 5 horas (18000s >= 14400s -> fridge_risk=true)
    assert _bb_cmd(root, "--step", "19060", "1", "1", "17", "4050", "30.0") == "POWER_RESTORED_REAL"

    pending = json.loads(_bb_cmd(root, "--pending"))
    assert len(pending) == 3
    restored = [ev for ev in pending if ev["event"] == "POWER_RESTORED"][0]
    assert restored["classification"] == "REAL_POWER_OUTAGE"
    assert restored["outage_duration_sec"] == 18000
    assert restored["fridge_risk"] is True
    assert restored["battery_start_pct"] == 74
    assert restored["battery_end_pct"] == 17


def test_android_blackbox_isp_only_outage(tmp_path: Path) -> None:
    root = tmp_path / "ns"
    assert _bb_cmd(root, "--step", "2000", "1", "1", "72", "4010", "32.0") == "NONE"
    # Internet cai, mas tomada permanece 100% online (ac=1)
    assert _bb_cmd(root, "--step", "2060", "1", "0", "72", "4010", "32.0") == "NONE"
    assert _bb_cmd(root, "--step", "2660", "1", "0", "72", "4010", "32.0") == "NONE"
    # Internet volta após 1200s (20 min) com AC estável o tempo todo
    assert _bb_cmd(root, "--step", "3260", "1", "1", "72", "4010", "32.0") == "ISP_RESTORED_ONLY"

    pending = json.loads(_bb_cmd(root, "--pending"))
    assert len(pending) == 1
    ev = pending[0]
    assert ev["event"] == "ISP_RESTORED"
    assert ev["classification"] == "ISP_ONLY_OUTAGE"
    assert ev["outage_duration_sec"] == 1200
    assert ev["ac_stayed_online"] is True


def test_hub_dead_man_switch_and_deduplication() -> None:
    sm = SentinelStateMachine(heartbeat_timeout_sec=180, alert_cooldown_sec=600, fridge_risk_outage_sec=14400)
    state = NodeRuntimeState(node_id="e7plus-jp-ap", location="Apê João Pessoa")

    hb1 = HeartbeatPayload.from_dict(
        {
            "node_id": "e7plus-jp-ap",
            "location": "Apê João Pessoa",
            "timestamp": 1000,
            "ac_online": True,
            "battery_pct": 72,
            "temp_c": 32.1,
            "voltage_mv": 4010,
            "uptime_sec": 3600,
            "wifi_rssi": -58,
            "battery_mode": "BYPASS_HOLD",
        }
    )
    assert sm.process_heartbeat(state, hb1, received_at=1000) == []
    assert state.status == "ONLINE"

    # Aos 179s ainda está dentro da tolerância
    assert sm.check_dead_man_switch(state, now_ts=1179) == []

    # Aos 180s dispara Dead Man's Switch exatamente 1 vez
    alerts = sm.check_dead_man_switch(state, now_ts=1180)
    assert len(alerts) == 1
    assert alerts[0].category == "DEAD_MAN_SWITCH"
    assert alerts[0].severity == "critical"
    assert "OFFLINE há 3 min" in alerts[0].body
    assert "Bateria 72%" in alerts[0].body
    assert "Tomada OK" in alerts[0].body

    # Ciclos seguintes enquanto ainda offline não podem gerar spam
    assert sm.check_dead_man_switch(state, now_ts=1240) == []
    assert sm.check_dead_man_switch(state, now_ts=1300) == []

    # Reconexão com descarga forense de queda elétrica real (5h -> risco geladeira)
    hb_return = HeartbeatPayload.from_dict(
        {
            "node_id": "e7plus-jp-ap",
            "location": "Apê João Pessoa",
            "timestamp": 19000,
            "ac_online": True,
            "battery_pct": 54,
            "temp_c": 31.0,
            "pending_blackbox_events": [
                {
                    "event": "POWER_RESTORED",
                    "classification": "REAL_POWER_OUTAGE",
                    "power_lost_ts": 1020,
                    "power_restored_ts": 19020,
                    "outage_duration_sec": 18000,
                    "battery_start_pct": 72,
                    "battery_end_pct": 54,
                    "fridge_risk": True,
                }
            ],
        }
    )
    return_alerts = sm.process_heartbeat(state, hb_return, received_at=19025)
    assert len(return_alerts) == 1
    assert return_alerts[0].category == "POWER_FORENSIC"
    assert return_alerts[0].severity == "critical"
    assert "QUEDA ELÉTRICA REAL" in return_alerts[0].body
    assert "GELADEIRA" in return_alerts[0].body

    # Reenvio duplicado do mesmo evento forense dentro do cooldown é deduplicado
    assert sm.process_heartbeat(state, hb_return, received_at=19085) == []

    # Queda de energia instantânea relatada enquanto online (evento POWER_LOST)
    hb_pwr_lost = HeartbeatPayload.from_dict(
        {
            "node_id": "e7plus-jp-ap",
            "location": "Apê João Pessoa",
            "timestamp": 20000,
            "ac_online": False,
            "battery_pct": 52,
            "temp_c": 31.2,
            "pending_blackbox_events": [
                {
                    "event": "POWER_LOST",
                    "ts": 20000,
                    "ac_online": False,
                    "battery_pct": 52,
                }
            ],
        }
    )
    pwr_lost_alerts = sm.process_heartbeat(state, hb_pwr_lost, received_at=20001)
    assert len(pwr_lost_alerts) == 1
    assert pwr_lost_alerts[0].category == "POWER_FORENSIC"
    assert "Tomada Sem Energia" in pwr_lost_alerts[0].title
    assert "52%" in pwr_lost_alerts[0].body
