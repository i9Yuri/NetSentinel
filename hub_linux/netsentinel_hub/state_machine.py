"""Máquina de estados do Dead Man's Switch, laudo forense de energia/ISP e deduplicação de alertas."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import AlertMessage, HeartbeatPayload


def format_duration(seconds: int) -> str:
    secs = max(0, int(seconds))
    hours, rem = divmod(secs, 3600)
    minutes, sec = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {sec}s"
    if minutes > 0:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


@dataclass
class NodeRuntimeState:
    node_id: str
    location: str = "Apê João Pessoa"
    status: str = "INIT"  # INIT | ONLINE | OFFLINE
    last_heartbeat_ts: int = 0
    last_received_at: int = 0
    last_ac_online: bool = True
    last_battery_pct: int = -1
    last_temp_c: float = 0.0
    last_wifi_rssi: int = -127
    last_battery_mode: str = "UNKNOWN"
    last_uptime_sec: int = 0
    last_voltage_mv: int = 0
    offline_since_ts: int = 0
    offline_alert_dispatched: bool = False
    dedup_timestamps: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "location": self.location,
            "status": self.status,
            "last_heartbeat_ts": self.last_heartbeat_ts,
            "last_received_at": self.last_received_at,
            "last_ac_online": self.last_ac_online,
            "last_battery_pct": self.last_battery_pct,
            "last_temp_c": self.last_temp_c,
            "last_wifi_rssi": self.last_wifi_rssi,
            "last_battery_mode": self.last_battery_mode,
            "last_uptime_sec": self.last_uptime_sec,
            "last_voltage_mv": self.last_voltage_mv,
            "offline_since_ts": self.offline_since_ts,
            "offline_alert_dispatched": self.offline_alert_dispatched,
            "dedup_timestamps": dict(self.dedup_timestamps),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> NodeRuntimeState:
        raw_dedup = data.get("dedup_timestamps")
        dedup: dict[str, int] = {}
        if isinstance(raw_dedup, dict):
            dedup = {str(k): int(v) for k, v in raw_dedup.items()}
        return cls(
            node_id=str(data.get("node_id", "e7plus-jp-ap")),
            location=str(data.get("location", "Apê João Pessoa")),
            status=str(data.get("status", "INIT")),
            last_heartbeat_ts=int(data.get("last_heartbeat_ts", 0) or 0),
            last_received_at=int(data.get("last_received_at", 0) or 0),
            last_ac_online=bool(data.get("last_ac_online", True)),
            last_battery_pct=int(data.get("last_battery_pct", -1)),
            last_temp_c=float(data.get("last_temp_c", 0.0) or 0.0),
            last_wifi_rssi=int(data.get("last_wifi_rssi", -127)),
            last_battery_mode=str(data.get("last_battery_mode", "UNKNOWN")),
            last_uptime_sec=int(data.get("last_uptime_sec", 0) or 0),
            last_voltage_mv=int(data.get("last_voltage_mv", 0) or 0),
            offline_since_ts=int(data.get("offline_since_ts", 0) or 0),
            offline_alert_dispatched=bool(data.get("offline_alert_dispatched", False)),
            dedup_timestamps=dedup,
        )


class SentinelStateMachine:
    """Processa heartbeats, detecta silêncio (Dead Man's Switch) e deduplica alertas."""

    def __init__(
        self,
        heartbeat_timeout_sec: int = 180,
        alert_cooldown_sec: int = 600,
        fridge_risk_outage_sec: int = 14400,
    ) -> None:
        self.heartbeat_timeout_sec = heartbeat_timeout_sec
        self.alert_cooldown_sec = alert_cooldown_sec
        self.fridge_risk_outage_sec = fridge_risk_outage_sec

    def _should_emit(self, state: NodeRuntimeState, dedup_key: str, now_ts: int) -> bool:
        last_ts = state.dedup_timestamps.get(dedup_key, 0)
        if last_ts > 0 and (now_ts - last_ts) < self.alert_cooldown_sec:
            return False
        state.dedup_timestamps[dedup_key] = now_ts
        return True

    def check_dead_man_switch(
        self, state: NodeRuntimeState, now_ts: int
    ) -> list[AlertMessage]:
        """Avalia se o nó ultrapassou o teto de silêncio (180s padrão)."""
        if state.last_received_at <= 0:
            return []

        silence_sec = now_ts - state.last_received_at
        if silence_sec < self.heartbeat_timeout_sec:
            return []

        alerts: list[AlertMessage] = []
        if state.status != "OFFLINE":
            state.status = "OFFLINE"
            state.offline_since_ts = state.last_received_at

        if not state.offline_alert_dispatched:
            state.offline_alert_dispatched = True
            silence_min = max(1, silence_sec // 60)
            ac_txt = "Tomada OK" if state.last_ac_online else "na bateria (sem tomada)"
            body = (
                f"🚨 [NetSentinel] O vigia de {state.location} está OFFLINE há {silence_min} min.\n"
                f"Último status registrado: Bateria {state.last_battery_pct}%, {ac_txt}.\n"
                f"Aguardando retorno da conexão para confirmar se foi falta de energia no prédio ou queda de internet."
            )
            alerts.append(
                AlertMessage(
                    dedup_key=f"offline:{state.node_id}:{state.offline_since_ts}",
                    severity="critical",
                    title="NetSentinel — Vigia Remoto Offline",
                    body=body,
                    category="DEAD_MAN_SWITCH",
                )
            )
        return alerts

    def process_heartbeat(
        self, state: NodeRuntimeState, hb: HeartbeatPayload, received_at: int
    ) -> list[AlertMessage]:
        """Ingere um heartbeat validado, atualiza estado e gera laudos/alertas deduplicados."""
        alerts: list[AlertMessage] = []
        was_offline = state.status == "OFFLINE"
        offline_duration = (
            max(0, received_at - state.offline_since_ts)
            if was_offline and state.offline_since_ts > 0
            else 0
        )

        # Atualiza telemetria mais recente
        state.node_id = hb.node_id
        state.location = hb.location or state.location
        state.status = "ONLINE"
        state.last_heartbeat_ts = hb.timestamp
        state.last_received_at = received_at
        state.last_ac_online = hb.ac_online
        state.last_battery_pct = hb.battery_pct
        state.last_temp_c = hb.temp_c
        state.last_wifi_rssi = hb.wifi_rssi
        state.last_battery_mode = hb.battery_mode
        state.last_uptime_sec = hb.uptime_sec
        state.last_voltage_mv = hb.voltage_mv
        state.offline_since_ts = 0
        state.offline_alert_dispatched = False

        had_forensic_outage_report = False

        # 1. Processa eventos forenses da caixa-preta local do Moto E7 Plus
        for ev in hb.pending_blackbox_events:
            if ev.event == "POWER_RESTORED" or ev.classification == "REAL_POWER_OUTAGE":
                had_forensic_outage_report = True
                dur = ev.outage_duration_sec
                fridge_risk = ev.fridge_risk or (dur >= self.fridge_risk_outage_sec)
                dedup_key = f"power_restored:{hb.node_id}:{ev.power_lost_ts}:{ev.power_restored_ts}"
                if self._should_emit(state, dedup_key, received_at):
                    fridge_line = (
                        "\n\n⚠️ ALERTA GELADEIRA/CONGELADOR: A falta de luz durou mais de 4 horas. Verifique os alimentos na geladeira e congelador!"
                        if fridge_risk
                        else ""
                    )
                    body = (
                        f"⚡ [NetSentinel] A luz voltou! — {state.location}\n"
                        f"Classificação: QUEDA ELÉTRICA REAL (confirmada no imóvel)\n"
                        f"Tempo total sem luz: {format_duration(dur)}\n"
                        f"Bateria do vigia: de {ev.battery_start_pct}% para {ev.battery_end_pct}%"
                        f"{fridge_line}"
                    )
                    alerts.append(
                        AlertMessage(
                            dedup_key=dedup_key,
                            severity="critical" if fridge_risk else "normal",
                            title="NetSentinel — Energia Restaurada",
                            body=body,
                            category="POWER_FORENSIC",
                        )
                    )
            elif ev.event == "POWER_LOST":
                dedup_key = f"power_lost:{hb.node_id}:{ev.power_lost_ts}"
                if self._should_emit(state, dedup_key, received_at):
                    batt_txt = f" ({ev.battery_start_pct}%)" if ev.battery_start_pct >= 0 else ""
                    body = (
                        f"🔌 [NetSentinel] Tomada sem energia! — {state.location}\n"
                        f"O vigia detectou que a tomada parou de fornecer energia.\n"
                        f"O aparelho passou a operar na bateria interna{batt_txt}."
                    )
                    alerts.append(
                        AlertMessage(
                            dedup_key=dedup_key,
                            severity="critical",
                            title="NetSentinel — Tomada Sem Energia",
                            body=body,
                            category="POWER_FORENSIC",
                        )
                    )
            elif ev.event == "ISP_RESTORED" or ev.classification == "ISP_ONLY_OUTAGE":
                had_forensic_outage_report = True
                dur = ev.outage_duration_sec
                dedup_key = f"isp_restored:{hb.node_id}:{ev.power_lost_ts}:{ev.power_restored_ts}"
                if self._should_emit(state, dedup_key, received_at):
                    body = (
                        f"🌐 [NetSentinel] A internet voltou! — {state.location}\n"
                        f"Status: Apenas queda do provedor de internet.\n"
                        f"A energia elétrica na tomada permaneceu 100% normal (nenhum risco para a geladeira).\n"
                        f"Tempo total sem conexão: {format_duration(dur)}"
                    )
                    alerts.append(
                        AlertMessage(
                            dedup_key=dedup_key,
                            severity="normal",
                            title="NetSentinel — Internet Restaurada",
                            body=body,
                            category="ISP_FORENSIC",
                        )
                    )

        # 2. Se estava OFFLINE no Hub mas voltou sem evento forense explícito da caixa-preta
        if was_offline and not had_forensic_outage_report:
            verdict = (
                "A tomada continuou com energia normal (provável oscilação rápida de internet)."
                if hb.ac_online
                else "O celular está operando na bateria (sem tomada)."
            )
            body = (
                f"✅ [NetSentinel] Conexão restabelecida com {state.location}!\n"
                f"Tempo fora do ar: {format_duration(offline_duration)}\n"
                f"Status atual: Bateria em {hb.battery_pct}% ({hb.temp_c:.1f} °C). {verdict}"
            )
            alerts.append(
                AlertMessage(
                    dedup_key=f"recovery:{hb.node_id}:{received_at}",
                    severity="normal",
                    title="NetSentinel — Vigia Reconectado",
                    body=body,
                    category="RECOVERY",
                )
            )

        # 3. Processa anomalias passivas de espectro (Wi-Fi / LAN / BLE)
        for anom in hb.spectrum_anomalies:
            dedup_key = f"spectrum:{hb.node_id}:{anom.source}:{anom.mac}"
            # Para dispositivos na LAN, alerta de 'Novo dispositivo' só deve ocorrer no primeiro avistamento
            if anom.source == "lan":
                if dedup_key in state.dedup_timestamps:
                    continue
                state.dedup_timestamps[dedup_key] = received_at
            elif not self._should_emit(state, dedup_key, received_at):
                continue

            if anom.source == "lan":
                body = (
                    f"⚠️ [NetSentinel] Novo dispositivo conectado na rede do apê!\n"
                    f"📍 Local: {state.location}\n"
                    f"Aparelho / Fabricante: {anom.vendor}\n"
                    f"Identificação: {anom.label}\n"
                    f"Endereço físico (MAC): {anom.mac}"
                )
            else:
                body = (
                    f"📡 [NetSentinel] Dispositivo próximo detectado\n"
                    f"📍 Local: {state.location}\n"
                    f"Rede / Identificação: {anom.label}\n"
                    f"Fabricante: {anom.vendor}\n"
                    f"Endereço físico (MAC): {anom.mac}"
                )
            alerts.append(
                AlertMessage(
                    dedup_key=dedup_key,
                    severity="critical",
                    title="NetSentinel — Dispositivo Detectado",
                    body=body,
                    category="SPECTRUM",
                )
            )

        # 4. Proteção térmica reportada pelo nó (>= 40.0 °C)
        if hb.temp_c >= 40.0 or hb.battery_mode == "THERMAL_CUT":
            dedup_key = f"thermal:{hb.node_id}"
            if self._should_emit(state, dedup_key, received_at):
                body = (
                    f"🌡️ [NetSentinel] Alerta de Temperatura — {state.location}\n"
                    f"A bateria atingiu {hb.temp_c:.1f} °C (limite de segurança: 40.0 °C).\n"
                    f"O carregamento foi pausado automaticamente pelo circuito do aparelho até esfriar."
                )
                alerts.append(
                    AlertMessage(
                        dedup_key=dedup_key,
                        severity="critical",
                        title="NetSentinel — Alerta de Temperatura",
                        body=body,
                        category="THERMAL",
                    )
                )

        return alerts
