"""Contratos de dados tipados do NetSentinel Hub."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class BlackboxEvent:
    event: str
    raw: dict[str, Any]
    classification: str = ""
    outage_duration_sec: int = 0
    power_lost_ts: int = 0
    power_restored_ts: int = 0
    battery_start_pct: int = -1
    battery_end_pct: int = -1
    fridge_risk: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BlackboxEvent:
        return cls(
            event=str(data.get("event", "UNKNOWN")),
            raw=dict(data),
            classification=str(data.get("classification", "")),
            outage_duration_sec=int(data.get("outage_duration_sec", 0) or 0),
            power_lost_ts=int(data.get("power_lost_ts", data.get("net_lost_ts", data.get("ts", 0))) or 0),
            power_restored_ts=int(data.get("power_restored_ts", data.get("net_restored_ts", 0)) or 0),
            battery_start_pct=int(data.get("battery_start_pct", data.get("battery_pct", -1))),
            battery_end_pct=int(data.get("battery_end_pct", data.get("battery_pct", -1))),
            fridge_risk=bool(data.get("fridge_risk", False)),
        )


@dataclass(frozen=True, slots=True)
class SpectrumAnomaly:
    ts: int
    source: str
    mac: str
    vendor: str
    rssi_max: int
    rssi_now: int
    persist_sec: int
    label: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpectrumAnomaly:
        return cls(
            ts=int(data.get("ts", 0) or 0),
            source=str(data.get("source", "wifi")),
            mac=str(data.get("mac", "")).lower(),
            vendor=str(data.get("vendor", "Unknown")),
            rssi_max=int(data.get("rssi_max", -127)),
            rssi_now=int(data.get("rssi_now", -127)),
            persist_sec=int(data.get("persist_sec", 0)),
            label=str(data.get("label", "")),
        )


@dataclass(frozen=True, slots=True)
class LanDevice:
    ip: str
    mac: str
    vendor: str
    category: str
    name: str
    is_known: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LanDevice:
        return cls(
            ip=str(data.get("ip", "")),
            mac=str(data.get("mac", "")).lower(),
            vendor=str(data.get("vendor", "Unknown")),
            category=str(data.get("category", "UNKNOWN")),
            name=str(data.get("name", "")),
            is_known=bool(data.get("is_known", True)),
        )


@dataclass(frozen=True, slots=True)
class HeartbeatPayload:
    node_id: str
    location: str
    timestamp: int
    ac_online: bool
    battery_pct: int
    temp_c: float
    voltage_mv: int
    uptime_sec: int
    wifi_rssi: int
    battery_mode: str = "UNKNOWN"
    pending_blackbox_events: tuple[BlackboxEvent, ...] = field(default_factory=tuple)
    spectrum_anomalies: tuple[SpectrumAnomaly, ...] = field(default_factory=tuple)
    lan_devices: tuple[LanDevice, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HeartbeatPayload:
        required = ("node_id", "timestamp", "ac_online", "battery_pct", "temp_c")
        for key in required:
            if key not in data:
                raise ValueError(f"Campo obrigatório ausente no heartbeat: {key}")

        bb_raw = data.get("pending_blackbox_events") or []
        sp_raw = data.get("spectrum_anomalies") or []
        lan_raw = data.get("lan_devices") or []
        if not isinstance(bb_raw, list) or not isinstance(sp_raw, list) or not isinstance(lan_raw, list):
            raise ValueError("pending_blackbox_events, spectrum_anomalies e lan_devices devem ser listas")

        return cls(
            node_id=str(data["node_id"]),
            location=str(data.get("location", "Apê João Pessoa")),
            timestamp=int(data["timestamp"]),
            ac_online=bool(data["ac_online"]),
            battery_pct=int(data["battery_pct"]),
            temp_c=float(data["temp_c"]),
            voltage_mv=int(data.get("voltage_mv", 0)),
            uptime_sec=int(data.get("uptime_sec", 0)),
            wifi_rssi=int(data.get("wifi_rssi", -127)),
            battery_mode=str(data.get("battery_mode", "UNKNOWN")),
            pending_blackbox_events=tuple(
                BlackboxEvent.from_dict(item) for item in bb_raw if isinstance(item, dict)
            ),
            spectrum_anomalies=tuple(
                SpectrumAnomaly.from_dict(item) for item in sp_raw if isinstance(item, dict)
            ),
            lan_devices=tuple(
                LanDevice.from_dict(item) for item in lan_raw if isinstance(item, dict)
            ),
        )


@dataclass(frozen=True, slots=True)
class AlertMessage:
    dedup_key: str
    severity: str  # "critical" | "normal"
    title: str
    body: str
    category: str  # "DEAD_MAN_SWITCH" | "POWER_FORENSIC" | "ISP_FORENSIC" | "SPECTRUM" | "THERMAL" | "RECOVERY"
