"""Servidor HTTP ultraleve (stdlib) + thread watchdog do Dead Man's Switch (< 25 MB RAM)."""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import HubConfig
from .models import AlertMessage, HeartbeatPayload
from .notifier import Notifier
from .security import verify_signature
from .state_machine import SentinelStateMachine
from .storage import HubStorage

logger = logging.getLogger("netsentinel.server")

MAX_BODY_BYTES = 262_144  # 256 KB proteção contra flood de memória


class NetSentinelHubService:
    """Coordena estado, armazenamento atômico, validação HMAC e watchdog."""

    def __init__(
        self,
        config: HubConfig,
        storage: HubStorage | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.config = config
        self.storage = storage or HubStorage(config.data_dir)
        self.notifier = notifier or Notifier(config)
        self.sm = SentinelStateMachine(
            heartbeat_timeout_sec=config.heartbeat_timeout_sec,
            alert_cooldown_sec=config.alert_cooldown_sec,
            fridge_risk_outage_sec=config.fridge_risk_outage_sec,
        )
        self.lock = threading.Lock()
        self.state = self.storage.load_state(config.node_id, config.location_label)
        self._stop_event = threading.Event()

    def ingest_raw_heartbeat(
        self,
        raw_body: bytes,
        signature_header: str,
        now_ts: int | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Valida HMAC-SHA256, parseia heartbeat, persiste e dispara alertas se houver."""
        current_ts = int(time.time()) if now_ts is None else int(now_ts)

        try:
            parsed = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_json"}

        if not isinstance(parsed, dict):
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "json_object_required"}

        try:
            hb = HeartbeatPayload.from_dict(parsed)
        except (ValueError, TypeError) as exc:
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)}

        sig_ok, reason = verify_signature(
            secret=self.config.hmac_secret,
            payload=raw_body,
            signature_header=signature_header,
            payload_timestamp=hb.timestamp,
            now_ts=current_ts,
            replay_window_sec=self.config.replay_window_sec,
        )
        if not sig_ok:
            return HTTPStatus.UNAUTHORIZED, {"ok": False, "error": reason}

        with self.lock:
            self.storage.record_heartbeat(hb, current_ts)
            alerts = self.sm.process_heartbeat(self.state, hb, current_ts)

            # Filtra alertas de 'Novo dispositivo' caso o aparelho já conste como conhecido no banco
            filtered_alerts: list[AlertMessage] = []
            for a in alerts:
                if a.category == "SPECTRUM" and ":lan:" in a.dedup_key:
                    mac = a.dedup_key.split(":lan:")[-1].strip().lower()
                    if self.storage.is_device_known(mac):
                        continue
                filtered_alerts.append(a)
            alerts = filtered_alerts

            # Monitoramento hostil de intrusos em quarentena
            for dev in hb.lan_devices:
                if self.storage.is_device_blocked(dev.mac, dev.ip):
                    dedup_key = f"intruder:{hb.node_id}:{dev.mac}"
                    if self.sm._should_emit(self.state, dedup_key, current_ts):
                        alerts.append(
                            AlertMessage(
                                dedup_key=dedup_key,
                                severity="critical",
                                title="NetSentinel — Alerta de Intruso",
                                body=(
                                    f"🚨 [NetSentinel] INTRUSO DETECTADO NA REDE!\n"
                                    f"📍 Local: {self.state.location}\n"
                                    f"O aparelho em quarentena ({dev.name}) acabou de se reconectar ao Wi-Fi!\n"
                                    f"• IP Atual: {dev.ip}\n"
                                    f"• Endereço MAC: {dev.mac}\n"
                                    f"• Fabricante: {dev.vendor}\n\n"
                                    f"🛡️ Acesse o roteador (http://192.168.0.1) para expulsá-lo definitivamente pelo Filtro MAC."
                                ),
                                category="INTRUDER",
                            )
                        )
            self.storage.save_state(self.state)

        self._dispatch_alerts(alerts, current_ts)
        return HTTPStatus.OK, {
            "ok": True,
            "node_id": hb.node_id,
            "received_at": current_ts,
            "alerts_emitted": len(alerts),
        }

    def run_watchdog_tick(self, now_ts: int | None = None) -> list[AlertMessage]:
        current_ts = int(time.time()) if now_ts is None else int(now_ts)
        with self.lock:
            alerts = self.sm.check_dead_man_switch(self.state, current_ts)
            if alerts:
                self.storage.save_state(self.state)
        self._dispatch_alerts(alerts, current_ts)
        return alerts

    def _dispatch_alerts(self, alerts: list[AlertMessage], now_ts: int) -> None:
        for alert in alerts:
            logger.warning("[%s] %s", alert.category, alert.body.replace("\n", " | "))
            self.storage.record_alert(alert, now_ts)
            self.notifier.dispatch(alert)

    def health_snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "ok": True,
                "service": "netsentinel-hub",
                "state": self.state.to_dict(),
            }

    def stop(self) -> None:
        self._stop_event.set()


class _DualStackHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], handler_cls: type[BaseHTTPRequestHandler]):
        host = server_address[0]
        if ":" in host:
            self.address_family = socket.AF_INET6
        else:
            self.address_family = socket.AF_INET
        super().__init__(server_address, handler_cls)

    def server_bind(self) -> None:
        if self.address_family == socket.AF_INET6:
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except OSError:
                pass
        super().server_bind()


def create_http_server(service: NetSentinelHubService) -> _DualStackHTTPServer:
    """Constrói servidor HTTP vinculado ao serviço NetSentinelHubService."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("HTTP %s - " + format, self.address_string(), *args)

        def _send_json(self, status: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path in ("/health", "/api/v1/health"):
                self._send_json(HTTPStatus.OK, service.health_snapshot())
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:
            if self.path != "/api/v1/heartbeat":
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_content_length"})
                return

            if length <= 0 or length > MAX_BODY_BYTES:
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"ok": False, "error": "payload_size_out_of_bounds"},
                )
                return

            raw_body = self.rfile.read(length)
            sig = self.headers.get("X-NetSentinel-Signature", "")
            status, resp = service.ingest_raw_heartbeat(raw_body, sig)
            self._send_json(status, resp)

    return _DualStackHTTPServer((service.config.bind_host, service.config.bind_port), Handler)


def run_forever(config: HubConfig) -> None:
    """Inicia servidor HTTP e watchdog loop bloqueante até SIGINT/SIGTERM."""
    service = NetSentinelHubService(config)
    httpd = create_http_server(service)

    def watchdog_loop() -> None:
        while not service._stop_event.wait(timeout=config.watchdog_poll_sec):
            try:
                service.run_watchdog_tick()
            except Exception as exc:  # pragma: no cover
                logger.exception("Erro no ciclo do watchdog: %s", exc)

    t = threading.Thread(target=watchdog_loop, name="netsentinel-watchdog", daemon=True)
    t.start()

    from .telegram_bot import TelegramBotWorker

    bot_worker = TelegramBotWorker(service)
    if bot_worker.is_enabled:
        t_bot = threading.Thread(target=bot_worker.run_polling_loop, name="netsentinel-telegram-bot", daemon=True)
        t_bot.start()

    logger.info(
        "NetSentinel Hub escutando em [%s]:%d (timeout Dead Man's Switch: %ds)",
        config.bind_host,
        config.bind_port,
        config.heartbeat_timeout_sec,
    )
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        httpd.server_close()
        logger.info("NetSentinel Hub encerrado.")
