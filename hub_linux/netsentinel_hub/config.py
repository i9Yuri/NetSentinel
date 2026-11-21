"""Carregamento tipado de configuração (.env) e verificação de permissões."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class HubConfig:
    node_id: str
    location_label: str
    hmac_secret: str
    telegram_bot_token: str
    telegram_chat_id: str
    bind_host: str
    bind_port: int
    heartbeat_timeout_sec: int
    watchdog_poll_sec: int
    replay_window_sec: int
    alert_cooldown_sec: int
    fridge_risk_outage_sec: int
    data_dir: Path
    enable_desktop_notify: bool


def parse_env_file(path: Path) -> dict[str, str]:
    """Lê arquivo .env simples (KEY=VALUE) sem dependências externas."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        values[key] = val
    return values


def check_env_permissions(path: Path) -> list[str]:
    """Retorna avisos caso o arquivo .env esteja legível por grupo/outros."""
    warnings: list[str] = []
    if not path.exists():
        return warnings
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        warnings.append(
            f"Permissões inseguras em {path} ({oct(mode)}). Recomendado: chmod 0600 {path}"
        )
    return warnings


def load_config(env_path: Path | None = None) -> HubConfig:
    """Carrega configuração priorizando variáveis de ambiente sobre .env."""
    file_vars: dict[str, str] = {}
    if env_path is not None:
        file_vars = parse_env_file(env_path)
    else:
        default_env = Path.cwd() / ".env"
        if default_env.is_file():
            file_vars = parse_env_file(default_env)

    def get(key: str, default: str = "") -> str:
        return os.environ.get(key, file_vars.get(key, default))

    def get_int(key: str, default: int) -> int:
        raw = get(key, str(default)).strip()
        return int(raw) if raw else default

    data_dir = Path(os.path.expanduser(get("HUB_DATA_DIR", "~/.local/share/netsentinel")))

    return HubConfig(
        node_id=get("NETSENTINEL_NODE_ID", "e7plus-jp-ap"),
        location_label=get("NETSENTINEL_LOCATION_LABEL", "Apê João Pessoa (Ponta de Campina)"),
        hmac_secret=get("NETSENTINEL_HMAC_SECRET", ""),
        telegram_bot_token=get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=get("TELEGRAM_CHAT_ID", ""),
        bind_host=get("HUB_BIND_HOST", "::"),
        bind_port=get_int("HUB_BIND_PORT", 8765),
        heartbeat_timeout_sec=get_int("HEARTBEAT_TIMEOUT_SEC", 180),
        watchdog_poll_sec=get_int("WATCHDOG_POLL_SEC", 10),
        replay_window_sec=get_int("REPLAY_WINDOW_SEC", 300),
        alert_cooldown_sec=get_int("ALERT_COOLDOWN_SEC", 600),
        fridge_risk_outage_sec=get_int("FRIDGE_RISK_OUTAGE_SEC", 14400),
        data_dir=data_dir,
        enable_desktop_notify=get("ENABLE_DESKTOP_NOTIFY", "0") in ("1", "true", "True", "yes"),
    )
