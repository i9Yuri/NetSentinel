"""Notificações KDE Desktop (notify-send) e Telegram Bot API via stdlib."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from .config import HubConfig
from .models import AlertMessage

logger = logging.getLogger("netsentinel.notifier")


class Notifier:
    """Despacha alertas para KDE Plasma (notify-send) e Telegram Bot API."""

    def __init__(self, config: HubConfig) -> None:
        self.config = config
        self._notify_bin = shutil.which("notify-send")

    def send_desktop(self, alert: AlertMessage) -> bool:
        if not self.config.enable_desktop_notify or not self._notify_bin:
            return False
        urgency = "critical" if alert.severity == "critical" else "normal"
        cmd = [
            self._notify_bin,
            "-a",
            "NetSentinel",
            "-u",
            urgency,
            alert.title,
            alert.body,
        ]
        try:
            subprocess.run(cmd, check=False, timeout=5)
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Falha no notify-send: %s", exc)
            return False

    def send_telegram(self, text: str) -> bool:
        token = self.config.telegram_bot_token
        chat_id = self.config.telegram_chat_id
        if not token or not chat_id:
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        form = urllib.parse.urlencode(
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        req = urllib.request.Request(url, data=form, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return bool(body.get("ok", False))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            # Nunca registra URL/token nos logs
            logger.warning("Falha ao enviar mensagem ao Telegram: %s", type(exc).__name__)
            return False

    def dispatch(self, alert: AlertMessage) -> dict[str, bool]:
        desktop_ok = self.send_desktop(alert)
        tg_ok = self.send_telegram(alert.body)
        return {"desktop": desktop_ok, "telegram": tg_ok}
