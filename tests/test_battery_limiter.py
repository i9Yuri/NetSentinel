"""Testes determinísticos do limitador de bateria 24/7 (PMI632 bypass + corte térmico 40°C)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BATTERY_SCRIPT = REPO_ROOT / "node_android" / "netsentinel" / "battery_limiter.sh"


def _setup_mock_sysfs(
    tmp_path: Path,
    *,
    capacity: int,
    temp_tenths: int,
    usb_present: int = 1,
    usb_online: int = 1,
    pc_online: int = 0,
    usb_voltage_uv: int = 5000000,
    charging_enabled: int = 1,
    input_suspend: int = 0,
) -> tuple[Path, Path]:
    ps_dir = tmp_path / "power_supply"
    for sub in ("battery", "usb", "pc_port"):
        (ps_dir / sub).mkdir(parents=True, exist_ok=True)

    (ps_dir / "battery" / "capacity").write_text(f"{capacity}\n", encoding="utf-8")
    (ps_dir / "battery" / "temp").write_text(f"{temp_tenths}\n", encoding="utf-8")
    (ps_dir / "battery" / "voltage_now").write_text("4050000\n", encoding="utf-8")
    (ps_dir / "battery" / "current_now").write_text("0\n", encoding="utf-8")
    (ps_dir / "battery" / "status").write_text("Charging\n", encoding="utf-8")
    (ps_dir / "battery" / "charging_enabled").write_text(f"{charging_enabled}\n", encoding="utf-8")
    (ps_dir / "battery" / "input_suspend").write_text(f"{input_suspend}\n", encoding="utf-8")

    (ps_dir / "usb" / "present").write_text(f"{usb_present}\n", encoding="utf-8")
    (ps_dir / "usb" / "online").write_text(f"{usb_online}\n", encoding="utf-8")
    (ps_dir / "usb" / "voltage_now").write_text(f"{usb_voltage_uv}\n", encoding="utf-8")
    (ps_dir / "pc_port" / "online").write_text(f"{pc_online}\n", encoding="utf-8")

    root_dir = tmp_path / "ns_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    return ps_dir, root_dir


def _run_limiter(ps_dir: Path, root_dir: Path, flag: str = "--apply") -> str:
    env = os.environ.copy()
    env["POWER_SUPPLY_DIR"] = str(ps_dir)
    env["NS_ROOT"] = str(root_dir)
    env["NS_STATE_DIR"] = str(root_dir / "state")
    env["NS_LOG_DIR"] = str(root_dir / "logs")
    res = subprocess.run(
        ["/bin/sh", str(BATTERY_SCRIPT), flag],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def test_bypass_hold_at_75_percent_and_hysteresis(tmp_path: Path) -> None:
    ps_dir, root_dir = _setup_mock_sysfs(tmp_path, capacity=75, temp_tenths=320, charging_enabled=1)

    # Aos 75%: ativa bypass real (charging_enabled=0, input_suspend=0)
    action = _run_limiter(ps_dir, root_dir)
    assert action == "BYPASS_HOLD"
    assert (ps_dir / "battery" / "charging_enabled").read_text().strip() == "0"
    assert (ps_dir / "battery" / "input_suspend").read_text().strip() == "0"

    # Aos 68% (faixa de histerese 61-74): mantém charging_enabled=0 sem oscilar
    (ps_dir / "battery" / "capacity").write_text("68\n", encoding="utf-8")
    action = _run_limiter(ps_dir, root_dir)
    assert action == "HYSTERESIS_KEEP"
    assert (ps_dir / "battery" / "charging_enabled").read_text().strip() == "0"

    # Aos 60%: reativa recarga (CHARGE_ON)
    (ps_dir / "battery" / "capacity").write_text("60\n", encoding="utf-8")
    action = _run_limiter(ps_dir, root_dir)
    assert action == "CHARGE_ON"
    assert (ps_dir / "battery" / "charging_enabled").read_text().strip() == "1"
    assert (ps_dir / "battery" / "input_suspend").read_text().strip() == "0"


def test_gentle_discharge_above_82_percent(tmp_path: Path) -> None:
    ps_dir, root_dir = _setup_mock_sysfs(tmp_path, capacity=96, temp_tenths=330)
    action = _run_limiter(ps_dir, root_dir)
    assert action == "GENTLE_DISCHARGE"
    assert (ps_dir / "battery" / "charging_enabled").read_text().strip() == "0"
    assert (ps_dir / "battery" / "input_suspend").read_text().strip() == "1"


def test_thermal_emergency_cut_and_recovery_hysteresis(tmp_path: Path) -> None:
    # Mesmo com bateria baixa (45%), >= 40.0 °C (400 décimos) deve cortar carga imediatamente
    ps_dir, root_dir = _setup_mock_sysfs(tmp_path, capacity=45, temp_tenths=405, charging_enabled=1)
    assert _run_limiter(ps_dir, root_dir) == "THERMAL_CUT"
    assert (ps_dir / "battery" / "charging_enabled").read_text().strip() == "0"

    # A 38.5 °C (ainda acima do limite de retorno 37.0 °C): permanece travado em THERMAL_CUT
    (ps_dir / "battery" / "temp").write_text("385\n", encoding="utf-8")
    assert _run_limiter(ps_dir, root_dir) == "THERMAL_CUT"
    assert (ps_dir / "battery" / "charging_enabled").read_text().strip() == "0"

    # Ao resfriar para 36.8 °C (<= 37.0 °C): libera recarga normalmente
    (ps_dir / "battery" / "temp").write_text("368\n", encoding="utf-8")
    assert _run_limiter(ps_dir, root_dir) == "CHARGE_ON"
    assert (ps_dir / "battery" / "charging_enabled").read_text().strip() == "1"


def test_ac_detection_decoupled_from_charging_enabled_zero(tmp_path: Path) -> None:
    # Caso validado na Fase 0: charging_enabled=0, usb/online=0 (porta USB), mas usb/present=1
    ps_dir, root_dir = _setup_mock_sysfs(
        tmp_path,
        capacity=75,
        temp_tenths=320,
        usb_present=1,
        usb_online=0,
        pc_online=1,
        charging_enabled=0,
    )
    status = _run_limiter(ps_dir, root_dir, "--status")
    assert "ac_present=1" in status

    # Queda real de tomada: usb/present=0, usb/online=0, pc_port/online=0, usb/voltage_now=0
    (ps_dir / "usb" / "present").write_text("0\n", encoding="utf-8")
    (ps_dir / "pc_port" / "online").write_text("0\n", encoding="utf-8")
    (ps_dir / "usb" / "voltage_now").write_text("0\n", encoding="utf-8")
    assert _run_limiter(ps_dir, root_dir) == "ON_BATTERY"
    assert "ac_present=0" in _run_limiter(ps_dir, root_dir, "--status")
