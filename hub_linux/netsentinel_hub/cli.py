"""CLI administrativa do NetSentinel Hub (run, status, audit, test-alert, verify-env)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .config import check_env_permissions, load_config
from .models import AlertMessage
from .notifier import Notifier
from .server import run_forever
from .state_machine import format_duration
from .storage import HubStorage


def _cmd_run(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    env_path = Path(args.env) if args.env else None
    if env_path is not None:
        for warn in check_env_permissions(env_path):
            logging.warning(warn)
    config = load_config(env_path)
    if not config.hmac_secret or config.hmac_secret.startswith("CHANGE_ME"):
        logging.error("NETSENTINEL_HMAC_SECRET não configurado ou usando valor padrão.")
        return 2
    run_forever(config)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    config = load_config(Path(args.env) if args.env else None)
    storage = HubStorage(config.data_dir)
    state = storage.load_state(config.node_id, config.location_label)
    now = int(time.time())
    age = (now - state.last_received_at) if state.last_received_at > 0 else -1

    if args.json:
        out = state.to_dict()
        out["seconds_since_last_heartbeat"] = age
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    ac_txt = "Tomada ligada (energia OK)" if state.last_ac_online else "Sem energia na tomada (na bateria)"
    print(f"Local:         {state.location} [{state.node_id}]")
    print(f"Estado:        {state.status}")
    if age >= 0:
        print(f"Último sinal:  há {format_duration(age)}")
        print(f"Energia:       {ac_txt}")
        print(f"Bateria:       {state.last_battery_pct}% ({state.last_temp_c:.1f} °C | Modo: {state.last_battery_mode})")
        print(f"Wi-Fi RSSI:    {state.last_wifi_rssi} dBm")
    else:
        print("Último sinal:  nenhum heartbeat recebido ainda")
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    config = load_config(Path(args.env) if args.env else None)
    storage = HubStorage(config.data_dir)
    summary = storage.fetch_recent_summary(limit=args.limit)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _cmd_test_alert(args: argparse.Namespace) -> int:
    config = load_config(Path(args.env) if args.env else None)
    notifier = Notifier(config)
    alert = AlertMessage(
        dedup_key="test-alert",
        severity="normal",
        title="NetSentinel — Teste de Alerta",
        body=f"✅ [NetSentinel] Alerta de teste disparado a partir do Hub ({config.location_label}).",
        category="TEST",
    )
    res = notifier.dispatch(alert)
    print(json.dumps(res, ensure_ascii=False))
    return 0


def _cmd_verify_env(args: argparse.Namespace) -> int:
    env_path = Path(args.env) if args.env else Path.cwd() / ".env"
    warnings = check_env_permissions(env_path)
    config = load_config(env_path if env_path.exists() else None)
    issues: list[str] = list(warnings)
    if not config.hmac_secret or config.hmac_secret.startswith("CHANGE_ME"):
        issues.append("NETSENTINEL_HMAC_SECRET ausente ou padrão.")
    if not config.telegram_bot_token or not config.telegram_chat_id:
        issues.append("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não configurado (alertas Telegram inativos).")

    if issues:
        for item in issues:
            print(f"[AVISO] {item}")
        return 1
    print(f"[OK] Ambiente verificado: node={config.node_id} bind=[{config.bind_host}]:{config.bind_port}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="netsentinel-hub", description="NetSentinel Control Hub CLI")
    parser.add_argument("--env", default=None, help="Caminho para o arquivo .env")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Inicia daemon HTTP + Dead Man's Switch")
    p_run.add_argument("-v", "--verbose", action="store_true")
    p_run.set_defaults(func=_cmd_run)

    p_status = sub.add_parser("status", help="Exibe estado atual do nó monitorado")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=_cmd_status)

    p_audit = sub.add_parser("audit", help="Exporta auditoria recente (heartbeats, quedas, espectro, alertas)")
    p_audit.add_argument("--limit", type=int, default=15)
    p_audit.set_defaults(func=_cmd_audit)

    p_test = sub.add_parser("test-alert", help="Dispara alerta de teste para KDE + Telegram")
    p_test.set_defaults(func=_cmd_test_alert)

    p_verify = sub.add_parser("verify-env", help="Audita permissões e chaves do .env")
    p_verify.set_defaults(func=_cmd_verify_env)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
