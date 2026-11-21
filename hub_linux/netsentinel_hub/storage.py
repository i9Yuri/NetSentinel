"""Persistência atômica em SQLite (WAL) e snapshot JSON (tmp + fsync + os.replace)."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .models import AlertMessage, HeartbeatPayload
from .state_machine import NodeRuntimeState


def atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
    """Grava JSON de forma atômica (arquivo temporário no mesmo diretório + fsync + os.replace)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, target)


class HubStorage:
    """Armazena histórico de heartbeats, eventos forenses, espectro e alertas em SQLite WAL."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "netsentinel.db"
        self.state_path = self.data_dir / "runtime_state.json"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=FULL;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS heartbeats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    received_at INTEGER NOT NULL,
                    node_id TEXT NOT NULL,
                    node_ts INTEGER NOT NULL,
                    ac_online INTEGER NOT NULL,
                    battery_pct INTEGER NOT NULL,
                    temp_c REAL NOT NULL,
                    voltage_mv INTEGER NOT NULL,
                    uptime_sec INTEGER NOT NULL,
                    wifi_rssi INTEGER NOT NULL,
                    battery_mode TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_hb_received ON heartbeats(received_at DESC);

                CREATE TABLE IF NOT EXISTS forensic_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    received_at INTEGER NOT NULL,
                    node_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    outage_duration_sec INTEGER NOT NULL,
                    fridge_risk INTEGER NOT NULL,
                    raw_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS spectrum_anomalies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    received_at INTEGER NOT NULL,
                    node_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    mac TEXT NOT NULL,
                    vendor TEXT NOT NULL,
                    rssi_max INTEGER NOT NULL,
                    persist_sec INTEGER NOT NULL,
                    label TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sent_at INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    dedup_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS lan_devices (
                    mac TEXT PRIMARY KEY,
                    ip TEXT NOT NULL,
                    vendor TEXT NOT NULL,
                    category TEXT NOT NULL,
                    name TEXT NOT NULL,
                    is_known INTEGER NOT NULL,
                    is_blocked INTEGER NOT NULL DEFAULT 0,
                    first_seen_ts INTEGER NOT NULL,
                    last_seen_ts INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_lan_last_seen ON lan_devices(last_seen_ts DESC);

                CREATE TABLE IF NOT EXISTS quarantine_devices (
                    mac TEXT PRIMARY KEY,
                    ip TEXT NOT NULL,
                    vendor TEXT NOT NULL,
                    name TEXT NOT NULL,
                    blocked_at INTEGER NOT NULL,
                    reason TEXT NOT NULL
                );
                """
            )
            try:
                conn.execute("ALTER TABLE lan_devices ADD COLUMN is_blocked INTEGER NOT NULL DEFAULT 0;")
            except sqlite3.OperationalError:
                pass

    def load_state(self, node_id: str, location: str) -> NodeRuntimeState:
        if self.state_path.is_file():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return NodeRuntimeState.from_dict(raw)
            except (json.JSONDecodeError, OSError):
                pass
        return NodeRuntimeState(node_id=node_id, location=location)

    def save_state(self, state: NodeRuntimeState) -> None:
        atomic_write_json(self.state_path, state.to_dict())

    def record_heartbeat(self, hb: HeartbeatPayload, received_at: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO heartbeats (
                    received_at, node_id, node_ts, ac_online, battery_pct,
                    temp_c, voltage_mv, uptime_sec, wifi_rssi, battery_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    received_at,
                    hb.node_id,
                    hb.timestamp,
                    1 if hb.ac_online else 0,
                    hb.battery_pct,
                    hb.temp_c,
                    hb.voltage_mv,
                    hb.uptime_sec,
                    hb.wifi_rssi,
                    hb.battery_mode,
                ),
            )
            for ev in hb.pending_blackbox_events:
                conn.execute(
                    """
                    INSERT INTO forensic_events (
                        received_at, node_id, event_type, classification,
                        outage_duration_sec, fridge_risk, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        received_at,
                        hb.node_id,
                        ev.event,
                        ev.classification,
                        ev.outage_duration_sec,
                        1 if ev.fridge_risk else 0,
                        json.dumps(ev.raw, ensure_ascii=False),
                    ),
                )
            for sp in hb.spectrum_anomalies:
                conn.execute(
                    """
                    INSERT INTO spectrum_anomalies (
                        received_at, node_id, source, mac, vendor,
                        rssi_max, persist_sec, label
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        received_at,
                        hb.node_id,
                        sp.source,
                        sp.mac,
                        sp.vendor,
                        sp.rssi_max,
                        sp.persist_sec,
                        sp.label,
                    ),
                )
            for dev in hb.lan_devices:
                conn.execute(
                    """
                    INSERT INTO lan_devices (
                        mac, ip, vendor, category, name, is_known, is_blocked, first_seen_ts, last_seen_ts
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                    ON CONFLICT(mac) DO UPDATE SET
                        ip = excluded.ip,
                        vendor = excluded.vendor,
                        category = excluded.category,
                        name = excluded.name,
                        is_known = CASE
                            WHEN lan_devices.is_blocked = 1 THEN 0
                            WHEN lan_devices.is_known = 1 THEN 1
                            ELSE excluded.is_known
                        END,
                        last_seen_ts = excluded.last_seen_ts
                    """,
                    (
                        dev.mac,
                        dev.ip,
                        dev.vendor,
                        dev.category,
                        dev.name,
                        1 if dev.is_known else 0,
                        received_at,
                        received_at,
                    ),
                )

    def record_alert(self, alert: AlertMessage, sent_at: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO alerts (sent_at, category, severity, dedup_key, title, body)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (sent_at, alert.category, alert.severity, alert.dedup_key, alert.title, alert.body),
            )

    def fetch_stats_24h(self, now_ts: int | None = None) -> dict[str, Any]:
        """Calcula métricas agregadas das últimas 24 horas a partir do SQLite."""
        import time
        current = int(time.time()) if now_ts is None else int(now_ts)
        since_ts = current - 86400

        with self._connect() as conn:
            # Estatísticas de bateria e temperatura
            hb_stats = conn.execute(
                """
                SELECT
                    COUNT(*) as count,
                    MIN(temp_c) as min_temp,
                    MAX(temp_c) as max_temp,
                    AVG(temp_c) as avg_temp,
                    MIN(battery_pct) as min_batt,
                    MAX(battery_pct) as max_batt
                FROM heartbeats
                WHERE received_at >= ?
                """,
                (since_ts,),
            ).fetchone()

            # Quedas de energia real
            pwr_events = conn.execute(
                """
                SELECT
                    COUNT(*) as count,
                    COALESCE(SUM(outage_duration_sec), 0) as total_sec,
                    COALESCE(MAX(outage_duration_sec), 0) as max_sec,
                    COALESCE(SUM(fridge_risk), 0) as fridge_risks
                FROM forensic_events
                WHERE received_at >= ? AND (classification = 'REAL_POWER_OUTAGE' OR event_type = 'POWER_RESTORED')
                """,
                (since_ts,),
            ).fetchone()

            # Quedas apenas de internet
            isp_events = conn.execute(
                """
                SELECT
                    COUNT(*) as count,
                    COALESCE(SUM(outage_duration_sec), 0) as total_sec,
                    COALESCE(MAX(outage_duration_sec), 0) as max_sec
                FROM forensic_events
                WHERE received_at >= ? AND (classification = 'ISP_ONLY_OUTAGE' OR event_type = 'ISP_RESTORED')
                """,
                (since_ts,),
            ).fetchone()

            # Dispositivos anômalos no espectro
            anom_count = conn.execute(
                "SELECT COUNT(*) as count FROM spectrum_anomalies WHERE received_at >= ?",
                (since_ts,),
            ).fetchone()["count"]

        return {
            "period_sec": 86400,
            "heartbeats_count": hb_stats["count"] if hb_stats else 0,
            "min_temp_c": round(hb_stats["min_temp"], 1) if hb_stats and hb_stats["min_temp"] is not None else 0.0,
            "max_temp_c": round(hb_stats["max_temp"], 1) if hb_stats and hb_stats["max_temp"] is not None else 0.0,
            "avg_temp_c": round(hb_stats["avg_temp"], 1) if hb_stats and hb_stats["avg_temp"] is not None else 0.0,
            "min_batt_pct": hb_stats["min_batt"] if hb_stats and hb_stats["min_batt"] is not None else -1,
            "max_batt_pct": hb_stats["max_batt"] if hb_stats and hb_stats["max_batt"] is not None else -1,
            "power_outages_count": pwr_events["count"] if pwr_events else 0,
            "power_outages_total_sec": pwr_events["total_sec"] if pwr_events else 0,
            "power_outages_max_sec": pwr_events["max_sec"] if pwr_events else 0,
            "fridge_risk_count": pwr_events["fridge_risks"] if pwr_events else 0,
            "isp_outages_count": isp_events["count"] if isp_events else 0,
            "isp_outages_total_sec": isp_events["total_sec"] if isp_events else 0,
            "isp_outages_max_sec": isp_events["max_sec"] if isp_events else 0,
            "spectrum_anomalies_count": anom_count,
        }

    def fetch_lan_devices(self, recent_window_sec: int = 1800, now_ts: int | None = None) -> list[dict[str, Any]]:
        """Retorna dispositivos observados na rede local ordenados por IP."""
        import time
        current = int(time.time()) if now_ts is None else int(now_ts)
        min_ts = current - recent_window_sec
        with self._connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM lan_devices WHERE last_seen_ts >= ? ORDER BY ip ASC",
                    (min_ts,),
                )
            ]

    def trust_all_current_lan_devices(self) -> int:
        """Marca todos os dispositivos observados atualmente na LAN como confiáveis (is_known = 1)."""
        with self._connect() as conn:
            cursor = conn.execute("UPDATE lan_devices SET is_known = 1 WHERE is_known = 0")
            return cursor.rowcount

    def find_device(self, target: str) -> dict[str, Any] | None:
        """Localiza um dispositivo por endereço IP ou MAC."""
        clean = target.strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM lan_devices WHERE LOWER(mac) = ? OR ip = ? ORDER BY last_seen_ts DESC LIMIT 1",
                (clean, clean),
            ).fetchone()
            if row:
                return dict(row)
            # Tenta também na tabela de quarentena
            q_row = conn.execute(
                "SELECT * FROM quarantine_devices WHERE LOWER(mac) = ? OR ip = ? LIMIT 1",
                (clean, clean),
            ).fetchone()
            if q_row:
                return dict(q_row)
        return None

    def block_device(
        self, target: str, reason: str = "Quarentena manual", now_ts: int | None = None
    ) -> dict[str, Any] | None:
        """Coloca um dispositivo em quarentena de segurança (bloqueado)."""
        import time

        current_ts = int(time.time()) if now_ts is None else int(now_ts)
        dev = self.find_device(target)
        target_clean = target.strip()

        if dev:
            mac = dev["mac"].lower()
            ip = dev.get("ip", "LAN")
            vendor = dev.get("vendor", "Desconhecido")
            name = dev.get("name", "Aparelho")
        else:
            # Se for um MAC válido (xx:xx:xx:xx:xx:xx)
            if ":" in target_clean and len(target_clean.split(":")) == 6:
                mac = target_clean.lower()
                ip = "LAN"
                vendor = "Desconhecido"
                name = "Aparelho"
            elif "." in target_clean and len(target_clean.split(".")) == 4 and all(part.isdigit() for part in target_clean.split(".")):
                ip = target_clean
                mac = f"ip:{target_clean}"
                vendor = "IP Bloqueado"
                name = f"Dispositivo ({target_clean})"
            else:
                return None

        with self._connect() as conn:
            conn.execute(
                "UPDATE lan_devices SET is_blocked = 1, is_known = 0 WHERE LOWER(mac) = ?",
                (mac,),
            )
            conn.execute(
                """
                INSERT INTO quarantine_devices (mac, ip, vendor, name, blocked_at, reason)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(mac) DO UPDATE SET
                    ip = excluded.ip,
                    vendor = excluded.vendor,
                    name = excluded.name,
                    blocked_at = excluded.blocked_at,
                    reason = excluded.reason
                """,
                (mac, ip, vendor, name, current_ts, reason),
            )

        return {
            "mac": mac,
            "ip": ip,
            "vendor": vendor,
            "name": name,
            "blocked_at": current_ts,
            "reason": reason,
        }

    def unblock_device(self, target: str) -> bool:
        """Remove um dispositivo da quarentena de segurança."""
        clean = target.strip().lower()
        with self._connect() as conn:
            c1 = conn.execute(
                "DELETE FROM quarantine_devices WHERE LOWER(mac) = ? OR ip = ?",
                (clean, clean),
            )
            c2 = conn.execute(
                "UPDATE lan_devices SET is_blocked = 0 WHERE LOWER(mac) = ? OR ip = ?",
                (clean, clean),
            )
            return bool(c1.rowcount > 0 or c2.rowcount > 0)

    def fetch_blocked_devices(self) -> list[dict[str, Any]]:
        """Retorna todos os dispositivos atualmente em quarentena de segurança."""
        with self._connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM quarantine_devices ORDER BY blocked_at DESC"
                )
            ]

    def is_device_known(self, mac: str) -> bool:
        """Verifica se um endereço MAC está registrado como conhecido (is_known = 1)."""
        clean_mac = mac.strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT is_known FROM lan_devices WHERE LOWER(mac) = ? LIMIT 1",
                (clean_mac,),
            ).fetchone()
            if row and row["is_known"] == 1:
                return True
        return False

    def is_device_blocked(self, mac: str, ip: str = "") -> bool:
        """Verifica se um endereço MAC ou IP está marcado como bloqueado/quarentena."""
        clean_mac = mac.strip().lower()
        clean_ip = ip.strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM quarantine_devices WHERE LOWER(mac) = ? OR (ip != 'LAN' AND ip = ?) LIMIT 1",
                (clean_mac, clean_ip),
            ).fetchone()
            if row:
                return True
            row_lan = conn.execute(
                "SELECT is_blocked FROM lan_devices WHERE LOWER(mac) = ? OR (ip != 'LAN' AND ip = ?) LIMIT 1",
                (clean_mac, clean_ip),
            ).fetchone()
            if row_lan and row_lan["is_blocked"] == 1:
                return True
        return False

    def fetch_recent_summary(self, limit: int = 10) -> dict[str, list[dict[str, Any]]]:
        with self._connect() as conn:
            hbs = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM heartbeats ORDER BY received_at DESC LIMIT ?", (limit,)
                )
            ]
            events = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM forensic_events ORDER BY received_at DESC LIMIT ?", (limit,)
                )
            ]
            anomalies = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM spectrum_anomalies ORDER BY received_at DESC LIMIT ?", (limit,)
                )
            ]
            alerts = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM alerts ORDER BY sent_at DESC LIMIT ?", (limit,)
                )
            ]
        return {
            "heartbeats": hbs,
            "forensic_events": events,
            "spectrum_anomalies": anomalies,
            "alerts": alerts,
        }
