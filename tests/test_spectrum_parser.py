"""Testes do parser de espectro (Wi-Fi/LAN/BLE), whitelist, persistência RSSI e OUI lookup."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SPECTRUM_SCRIPT = REPO_ROOT / "node_android" / "netsentinel" / "spectrum_auditor.sh"
OUI_TABLE = REPO_ROOT / "node_android" / "netsentinel" / "oui_table.tsv"

SAMPLE_WIFI_SCAN = """\
    BSSID              Frequency      RSSI           Age(sec)     SSID                                 Flags
  24:2f:d0:76:41:b4       2457    -42(0:-42)            0.887    brisa-1082152                     [WPA2-PSK-CCMP-128][ESS]
  00:1c:b3:aa:bb:cc       2437    -48(0:-48)            1.120    iPhone de Desconhecido            [WPA2-PSK-CCMP-128][ESS]
  10:98:5f:9b:5e:7f       2437    -74(0:-74)            1.524    VIZINHO_LONGE                     [WPA2-PSK-CCMP-128][ESS]
"""

SAMPLE_NEIGH_OK = """\
192.168.0.1 dev wlan0 lladdr 24:2f:d0:76:41:b4 REACHABLE
"""

SAMPLE_NEIGH_INTRUDER = """\
192.168.0.1 dev wlan0 lladdr 24:2f:d0:76:41:b4 REACHABLE
192.168.0.55 dev wlan0 lladdr a4:83:e7:11:22:33 REACHABLE
"""


def _run_spectrum(
    tmp_path: Path,
    *,
    wifi_out: str,
    neigh_out: str,
    bt_out: str = "",
    args: tuple[str, ...] = ("--scan", "1000"),
) -> str:
    root = tmp_path / "ns"
    root.mkdir(parents=True, exist_ok=True)
    wifi_file = tmp_path / "wifi.txt"
    neigh_file = tmp_path / "neigh.txt"
    bt_file = tmp_path / "bt.txt"
    wifi_file.write_text(wifi_out, encoding="utf-8")
    neigh_file.write_text(neigh_out, encoding="utf-8")
    bt_file.write_text(bt_out, encoding="utf-8")

    env = os.environ.copy()
    env["NS_ROOT"] = str(root)
    env["NS_STATE_DIR"] = str(root / "state")
    env["NS_LOG_DIR"] = str(root / "logs")
    env["OUI_TABLE_FILE"] = str(OUI_TABLE)
    env["WHITELIST_FILE"] = str(root / "whitelist.conf")
    env["SPECTRUM_SKIP_TRIGGER"] = "1"
    env["SPECTRUM_SCAN_WIFI_APS"] = "1"
    env["WIFI_SCAN_LIST_CMD"] = f"cat '{wifi_file}'"
    env["NEIGH_CMD"] = f"cat '{neigh_file}'"
    env["BT_DUMP_CMD"] = f"cat '{bt_file}'"

    res = subprocess.run(
        ["/bin/sh", str(SPECTRUM_SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def test_wifi_proximity_persistence_whitelist_and_cooldown(tmp_path: Path) -> None:
    root = tmp_path / "ns"
    root.mkdir(parents=True, exist_ok=True)
    # Roteador conhecido na whitelist (mesmo com RSSI forte -42 dBm, não deve alertar)
    (root / "whitelist.conf").write_text("24:2f:d0:76:41:b4 # meu roteador\n", encoding="utf-8")

    # t=1000: primeira aparição do iPhone desconhecido (-48 dBm) -> ainda sem alerta (persist=0s < 180s)
    assert _run_spectrum(tmp_path, wifi_out=SAMPLE_WIFI_SCAN, neigh_out=SAMPLE_NEIGH_OK, args=("--scan", "1000")) == "0"

    # t=1120: 120s de persistência -> ainda abaixo de 180s
    assert _run_spectrum(tmp_path, wifi_out=SAMPLE_WIFI_SCAN, neigh_out=SAMPLE_NEIGH_OK, args=("--scan", "1120")) == "0"

    # t=1180: atingiu 180s de persistência -> dispara 1 anomalia (ignora vizinho -74 dBm e roteador whitelisted)
    assert _run_spectrum(tmp_path, wifi_out=SAMPLE_WIFI_SCAN, neigh_out=SAMPLE_NEIGH_OK, args=("--scan", "1180")) == "1"

    anomalies = json.loads(
        _run_spectrum(tmp_path, wifi_out=SAMPLE_WIFI_SCAN, neigh_out=SAMPLE_NEIGH_OK, args=("--pending",))
    )
    assert len(anomalies) == 1
    a = anomalies[0]
    assert a["mac"] == "00:1c:b3:aa:bb:cc"
    assert a["vendor"] == "Apple"
    assert a["rssi_max"] == -48
    assert a["persist_sec"] == 180
    assert a["label"] == "iPhone de Desconhecido"

    # t=1240: ainda dentro do cooldown (900s) -> não duplica alerta
    assert _run_spectrum(tmp_path, wifi_out=SAMPLE_WIFI_SCAN, neigh_out=SAMPLE_NEIGH_OK, args=("--scan", "1240")) == "0"


def test_unknown_lan_neighbor_and_learn_whitelist(tmp_path: Path) -> None:
    root = tmp_path / "ns"
    root.mkdir(parents=True, exist_ok=True)
    (root / "whitelist.conf").write_text("24:2f:d0:76:41:b4\n", encoding="utf-8")

    # Dispositivo desconhecido entra na LAN -> anomalia imediata
    found = _run_spectrum(tmp_path, wifi_out="", neigh_out=SAMPLE_NEIGH_INTRUDER, args=("--scan", "2000"))
    assert found == "1"

    # Após o primeiro alerta de novo dispositivo na LAN, ciclos subsequentes (mesmo após 900s de cooldown) NÃO re-alertam o mesmo aparelho
    assert _run_spectrum(tmp_path, wifi_out="", neigh_out=SAMPLE_NEIGH_INTRUDER, args=("--scan", "3100")) == "0"

    anomalies = json.loads(
        _run_spectrum(tmp_path, wifi_out="", neigh_out=SAMPLE_NEIGH_INTRUDER, args=("--pending",))
    )
    assert len(anomalies) == 1
    assert anomalies[0]["source"] == "lan"
    assert anomalies[0]["mac"] == "a4:83:e7:11:22:33"
    assert anomalies[0]["vendor"] == "Apple"

    # --learn-whitelist adiciona os dispositivos presentes e silencia futuros ciclos
    assert _run_spectrum(tmp_path, wifi_out=SAMPLE_WIFI_SCAN, neigh_out=SAMPLE_NEIGH_INTRUDER, args=("--learn-whitelist",)) == "WHITELIST_UPDATED"
    wl_text = (root / "whitelist.conf").read_text(encoding="utf-8")
    assert "00:1c:b3:aa:bb:cc" in wl_text
    assert "a4:83:e7:11:22:33" in wl_text
