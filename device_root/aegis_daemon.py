#!/usr/bin/env python3
"""Auxiliar stdlib do Aegis e servidor local do painel."""
import argparse
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import re
import shlex
import socket
import sqlite3
import subprocess
import sys
import threading
import time

BASE = Path(__file__).resolve().parent
LOCAL_DB = BASE / "data" / "metrics.db"
serial = os.environ.get("AEGIS_ADB_SERIAL") or ("192.168.0.4:5555" if "192.168.0.4:5555" in os.popen("adb devices").read() else "ZF52366RHQ")
ADB = ("adb", "-s", serial)
REMOTE = "/data/adb/aegis_sentinel"
DASHBOARD = BASE.parent / "host_tools/dashboard/index.html"
REMOTE_DB = f"""
db=$(/system/bin/sqlite3 -batch -bail :memory: \
    "SELECT json_extract(CAST(readfile('{REMOTE}/config.json') AS TEXT), '$.metrics_db');")
test -n "$db" && test -f "$db" || exit 1
"""
GATEWAY_SQL = (f"SELECT coalesce(json_extract(CAST(readfile('{REMOTE}/config.json') AS TEXT), "
               "'$.gateway_ip'), '192.168.0.1');")
STATUS_SQL = """
WITH recent AS (
    SELECT id, at, severe FROM network_cycles ORDER BY id DESC LIMIT 60
)
SELECT json_object(
    'power', (SELECT json_object('at', sampled_at, 'ac', ac, 'battery', battery_pct,
                                'temperature', temp_tenths / 10.0)
                FROM power_status WHERE id=1),
    'cycle', (SELECT json_object('id', id, 'at', at, 'severe', severe) FROM recent ORDER BY id DESC LIMIT 1),
    'history', (SELECT json_group_array(json_object(
        'cycle_id', cycle_id, 'at', at, 'role', role, 'target', target,
        'rtt_ms', rtt_ms, 'jitter_ms', jitter_ms, 'loss_pct', loss_pct))
        FROM (SELECT c.at, m.* FROM recent c JOIN network_metrics m ON m.cycle_id=c.id
              ORDER BY c.id, m.role, m.target))
)
"""


def sqlite_path(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def sqlite_cli(database: str) -> None:
    """Suporta as instruções SQL e readfile usados pelo daemon POSIX."""
    with closing(sqlite3.connect(database, timeout=5, isolation_level=None)) as connection:
        connection.create_function("readfile", 1, lambda path: Path(path).read_bytes())

        def execute(statement: str) -> None:
            for row in connection.execute(statement):
                print("|".join("" if value is None else str(value) for value in row))

        statement = ""
        for line in sys.stdin:
            while (separator := line.find(";")) >= 0:
                statement += line[:separator + 1]
                line = line[separator + 1:]
                if sqlite3.complete_statement(statement):
                    execute(statement)
                    statement = ""
            statement += line
        if statement.strip():
            execute(statement)


def send_wol(mac: str, destination: str) -> None:
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", mac):
        raise ValueError("MAC inválido")
    destination = str(IPv4Address(destination))
    packet = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.settimeout(3)
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if connection.sendto(packet, (destination, 9)) != len(packet):
            raise OSError("envio incompleto")


def remote_command(script: str) -> tuple[str, ...]:
    return (*ADB, "exec-out", "su -c " + shlex.quote("set -e\n" + script))


def read_status(path: Path, android: bool) -> dict:
    if android:
        output = subprocess.check_output(
            remote_command(f'{REMOTE_DB}\nexec /system/bin/sqlite3 -readonly -batch -bail "$db" '
                           + shlex.quote(STATUS_SQL)),
            stderr=subprocess.DEVNULL, timeout=10,
        )
        status = json.loads(output)
    elif path.exists():
        with closing(sqlite3.connect(sqlite_path(path), uri=True, timeout=5)) as connection:
            status = json.loads(connection.execute(STATUS_SQL).fetchone()[0])
    else:
        status = {"power": None, "cycle": None, "history": []}
    return {"source": "Moto E7 Plus · ADB" if android else "SQLite local", **status}


def run_wol(args: argparse.Namespace) -> str:
    if args.adb:
        command = remote_command(f"exec /system/bin/sh {REMOTE}/modules/wol_bridge.sh --check")
        return subprocess.check_output(command, timeout=30, text=True, stderr=subprocess.DEVNULL).strip()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    desktop = str(IPv4Address(config["xeon_ip"]))
    send_wol(config["xeon_mac"], config["broadcast_ip"])
    time.sleep(15)
    ping = subprocess.run(["ping", "-n", "-c", "1", "-W", "2", desktop], timeout=5,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    result = "respondeu ao ping" if ping.returncode == 0 else "sem resposta ICMP"
    return f"Magic Packet enviado. Workstation {result} após 15 s ({desktop})."


def probe_gateway(args: argparse.Namespace) -> str:
    """Sonda manual do gateway, sem inserir amostras no histórico agendado."""
    if args.adb:
        script = (
            f'. {REMOTE}/modules/network_auditor.sh\n'
            f'gateway=$(/system/bin/sqlite3 -batch -bail :memory: {shlex.quote(GATEWAY_SQL)})\n'
            'case "$gateway" in ""|*[!0-9.]*) exit 1 ;; esac\n'
            'valid_ipv4 "$gateway"\nping_metrics "$gateway" 5'
        )
        command = remote_command(script)
    else:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        gateway = str(IPv4Address(config.get("gateway_ip", "192.168.0.1")))
        command = ("sh", "-c", '. "$1"; ping_metrics "$2" 5', "aegis",
                   str(BASE / "modules/network_auditor.sh"), gateway)
    result = subprocess.check_output(command, timeout=18, text=True,
                                     stderr=subprocess.DEVNULL, env={**os.environ, "LC_ALL": "C"})
    readings = result.strip().split(",")
    if len(readings) != 3 or readings == ["NULL"] * 3:
        raise ValueError("sem resultado ICMP")
    values = ["—" if value == "NULL" else f"{float(value):.2f}".replace(".", ",") + unit
              for value, unit in zip(readings, (" ms", " ms", "%"))]
    return f"Gateway: RTT {values[0]}, jitter {values[1]}, perda {values[2]}."


def serve(args: argparse.Namespace) -> None:
    # Loopback, Host e Origin exatos. Requisições externas não podem acionar comandos.
    page = DASHBOARD.read_bytes()
    address = f"127.0.0.1:{args.port}"
    action_lock = threading.Lock()
    actions = {"/api/wol": ("wol", run_wol), "/api/probe": ("probe", probe_gateway)}

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            self.request.settimeout(5)
            super().setup()

        def log_message(self, fmt: str, *values: object) -> None:
            pass

        def respond(self, status: int, data: bytes | dict) -> None:
            is_json = isinstance(data, dict)
            payload = json.dumps(data, ensure_ascii=False).encode() if is_json else data
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8" if is_json
                             else "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; connect-src 'self'; script-src 'unsafe-inline'; "
                             "style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            if self.headers.get_all("Host") != [address]:
                self.respond(403, {"error": "Endereço do painel inválido."})
            elif self.path == "/":
                self.respond(200, page)
            elif self.path == "/api/status":
                try:
                    status = read_status(args.db, args.adb)
                except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError):
                    self.respond(503, {"error": "Verifique a conexão ADB ou a disponibilidade do SQLite."})
                else:
                    self.respond(200, status)
            else:
                self.respond(404, {"error": "Rota indisponível."})

        def do_POST(self) -> None:
            action = actions.get(self.path)
            if (self.headers.get_all("Host") != [address]
                    or self.headers.get_all("Origin") != [f"http://{address}"]):
                self.respond(403, {"error": "Origem do comando inválida."})
            elif action is None:
                self.respond(404, {"error": "Rota indisponível."})
            elif self.headers.get_all("X-Aegis-Action") != [action[0]]:
                self.respond(403, {"error": "Comando inválido."})
            elif not action_lock.acquire(blocking=False):
                self.respond(409, {"error": "Já existe um comando em andamento."})
            else:
                try:
                    self.respond(200, {"message": action[1](args)})
                except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                    self.respond(503, {"error": "Falha no comando. Verifique a conexão e a configuração."})
                finally:
                    action_lock.release()

    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        print(f"http://{address}/", flush=True)
        server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", metavar="DB", help=argparse.SUPPRESS)
    parser.add_argument("--send-wol", nargs=2, metavar=("MAC", "DESTINO"), help=argparse.SUPPRESS)
    parser.add_argument("command", nargs="?", choices=("status", "network", "wol", "serve"))
    parser.add_argument("--adb", action="store_true", help="usa o Moto E7 Plus para status e WoL")
    parser.add_argument("--db", type=Path, default=Path(os.getenv("AEGIS_DB") or LOCAL_DB))
    parser.add_argument("--config", type=Path, default=Path(os.getenv("AEGIS_CONFIG") or BASE / "config.json"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        if args.sqlite is not None:
            sqlite_cli(args.sqlite)
        elif args.send_wol:
            send_wol(*args.send_wol)
        elif args.command == "status":
            print(json.dumps(read_status(args.db, args.adb), ensure_ascii=False, indent=2))
        elif args.command == "network":
            if args.adb:
                parser.error("network executa a coleta local. Use status --adb para ler a coleta do aparelho.")
            env = {**os.environ, "AEGIS_PYTHON": sys.executable,
                   "AEGIS_DB": str(args.db.resolve()), "AEGIS_CONFIG": str(args.config.resolve())}
            return subprocess.run(["sh", str(BASE / "aegis_daemon.sh"), "--network-once"],
                                  env=env, check=False).returncode
        elif args.command == "wol":
            print(run_wol(args))
        elif args.command == "serve":
            if not 1 <= args.port <= 65535:
                parser.error("porta fora do intervalo 1–65535")
            serve(args)
        else:
            parser.error("informe um comando")
    except (OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError):
        print("Aegis: falha na operação. Verifique a configuração e a origem dos dados.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
