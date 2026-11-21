#!/usr/bin/env python3
"""Gera resumo executivo e histórico de telemetria Aegis, em UTC."""
import argparse
from collections import defaultdict
from contextlib import closing
from datetime import UTC, datetime
import hashlib
from html import escape
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "device_root"))
from aegis_daemon import LOCAL_DB, REMOTE_DB, remote_command, sqlite_path

ROLES = {"gateway": "Gateway local", "dns": "Servidor DNS (ICMP)", "ix": "Ponto de troca",
         "desktop": "Workstation local"}
BACKUP = REMOTE_DB + """
work=$(mktemp -d /data/adb/aegis_sentinel/data/report.XXXXXX)
trap 'rm -rf -- "$work"' EXIT
/system/bin/sqlite3 -readonly -batch -bail -cmd '.timeout 5000' "$db" ".backup '$work/metrics.db'"
cat "$work/metrics.db"
"""


def parse_time(value: str) -> int:
    try:
        stamp = datetime.fromisoformat(value)
        return int(stamp.replace(tzinfo=UTC).timestamp() if stamp.tzinfo is None else stamp.timestamp())
    except (OverflowError, ValueError) as error:
        raise argparse.ArgumentTypeError("use uma data ISO 8601, por exemplo 2026-11-21T00:00:00Z") from error


def utc(value: int) -> str:
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def number(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}".replace(".", ",")


def read_metrics(path: Path, start: int | None, end: int | None) -> tuple[list, list]:
    with closing(sqlite3.connect(sqlite_path(path), uri=True)) as db:
        db.execute("BEGIN")
        cycles = db.execute(
            """SELECT id, at, severe FROM network_cycles
               WHERE (? IS NULL OR at >= ?) AND (? IS NULL OR at < ?) ORDER BY at, id""",
            (start, start, end, end),
        ).fetchall()
        rows = db.execute(
            """SELECT c.id, c.at, m.role, m.target, m.rtt_ms, m.jitter_ms, m.loss_pct
               FROM network_cycles c JOIN network_metrics m ON m.cycle_id=c.id
               WHERE (? IS NULL OR c.at >= ?) AND (? IS NULL OR c.at < ?)
               ORDER BY c.at, c.id, m.role, m.target""",
            (start, start, end, end),
        ).fetchall()
    return cycles, rows


def table(headers: list[str], rows: list[list[str]]) -> str:
    def cells(values: list[str], tag: str) -> str:
        attr = ' scope="col"' if tag == "th" else ""
        return "".join(f"<{tag}{attr}>{escape(str(value))}</{tag}>" for value in values)
    content = "\n".join(f"<tr>{cells(values, 'td')}</tr>" for values in rows)
    return f"<table><thead><tr>{cells(headers, 'th')}</tr></thead><tbody>{content}</tbody></table>"


def build_report(cycles: list, rows: list, source: str, start: int | None, end: int | None) -> tuple[str, str]:
    groups = defaultdict(list)
    for _, _, role, target, *values in rows:
        groups[role, target].append(values)

    def aggregate(values: tuple) -> str:
        valid = [value for value in values if value is not None]
        if not valid:
            return "— (n=0)"
        return f"{number(sum(valid) / len(valid))} / {number(max(valid))} (n={len(valid)})"

    summary = [
        [ROLES.get(role, role), target, len(samples), *(aggregate(values) for values in zip(*samples))]
        for (role, target), samples in sorted(groups.items())
    ]
    history = [[cycle, utc(at), ROLES.get(role, role), target, *(number(value) for value in values)]
               for cycle, at, role, target, *values in rows]
    digest = hashlib.sha256(json.dumps([cycles, rows], separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    period = f"{utc(cycles[0][1])} a {utc(cycles[-1][1])}" if cycles else "Sem ciclos no período."
    window = f"{utc(start) if start is not None else 'sem limite inicial'} até {utc(end) if end is not None else 'sem limite final'}"
    recorded = f"{len(cycles)} ciclos, {len(rows)} medições por destino e {sum(severe == 1 for _, _, severe in cycles)} ciclos marcados como severos pelo coletor."
    generated = utc(int(datetime.now(UTC).timestamp()))
    method = (
        "O coletor usa sondas ICMP com carga útil de 64 bytes, 10 por destino de rede e 1 para a workstation. "
        "O RTT é a média das respostas por ciclo. O jitter é a média da diferença absoluta entre RTTs consecutivos. "
        "A perda vem da estatística do ping. As médias da tabela dão o mesmo peso a cada ciclo com valor disponível. "
        "n indica a quantidade de valores registrados por métrica. Campos marcados com — ficaram sem leitura."
    )
    severity = (
        "O coletor marca degradação severa quando o mesmo destino, exceto a workstation, registra perda acima de 10% "
        "ou jitter acima de 60 ms por três ciclos consecutivos."
    )
    limits = (
        "O histórico usa os horários registrados pelo dispositivo. Intervalos sem coleta ficam fora dos cálculos. "
        "As sondas registram o comportamento dos destinos a partir do ponto de coleta. A atribuição de causa "
        "depende de evidências complementares sobre a rede local e o trajeto até os destinos."
    )
    headers = ["Papel", "Destino", "Amostras", "RTT ms (média / máx.)",
               "Jitter ms (média / máx.)", "Perda % (média / máx.)"]
    metadata = [
        f"Gerado em {generated}. Fonte: {source}.",
        f"Janela consultada: {window}. O limite final é exclusivo.",
        f"Período observado: {period}", recorded,
        f"SHA-256 dos registros selecionados: {digest}",
    ]
    executive_rows = [" | ".join(headers), " | ".join(["---"] * len(headers))]
    for row in summary:
        executive_rows.append(" | ".join(str(value).replace("|", "&#124;").replace("\n", " ") for value in row))
    executive = "\n\n".join([
        "# Relatório executivo de conectividade",
        "\n".join(f"- {line}" for line in metadata),
        "## Resumo por destino",
        "\n".join(executive_rows) if summary else "Sem medições na janela consultada.",
        "## Método de coleta", method, severity, limits,
        "O histórico por ciclo acompanha o relatório em `relatorio.html`.",
    ]) + "\n"
    html = f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Relatório técnico de conectividade</title>
<style>
:root{{--ink:#17212b;--rule:#8a96a3;--stripe:#eff3f6;--paper:#fff;--font:system-ui,sans-serif}}
body{{font:1rem/1.55 var(--font);color:var(--ink);background:var(--paper);max-inline-size:75rem;margin:auto;padding:2rem 1rem}}
h1,h2{{line-height:1.2}} h2{{margin-block-start:2rem}} p{{max-inline-size:88ch}} li{{overflow-wrap:anywhere}}
table{{border-collapse:collapse;inline-size:100%;font-variant-numeric:tabular-nums}} .table{{overflow:auto}}
th,td{{border-block-end:1px solid var(--rule);padding:.45rem;text-align:start}} tr:nth-child(even) td{{background:var(--stripe)}}
@media print{{body{{padding:0}} .table{{overflow:visible}} thead{{display:table-header-group}} tr{{break-inside:avoid}}}}
</style></head><body><main><h1>Relatório técnico de conectividade</h1>
<ul>{''.join(f'<li>{escape(line)}</li>' for line in metadata)}</ul>
<h2>Resumo por destino</h2>
<div class="table">{table(headers, summary) if summary else '<p>Sem medições na janela consultada.</p>'}</div>
<h2>Método de coleta</h2><p>{escape(method)}</p><p>{escape(severity)}</p><p>{escape(limits)}</p>
<h2>Histórico por ciclo</h2><div class="table">
{table(['Ciclo','Data e hora','Papel','Destino','RTT (ms)','Jitter (ms)','Perda (%)'], history) if history else '<p>Sem histórico disponível.</p>'}
</div></main></body></html>
"""
    return executive, html


def atomic_write(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
        temp = Path(output.name)
        try:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
            output.close()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--db", type=Path, help=f"banco local (padrão: {LOCAL_DB})")
    source.add_argument("--adb", action="store_true", help="faz backup online no Moto E7 Plus")
    parser.add_argument("--from", dest="start", type=parse_time, help="início inclusivo em ISO 8601")
    parser.add_argument("--to", dest="end", type=parse_time, help="fim exclusivo em ISO 8601")
    parser.add_argument("--output-dir", type=Path, default=Path("relatorio-aegis"))
    args = parser.parse_args()
    if args.start is not None and args.end is not None and args.start >= args.end:
        parser.error("--from deve ser anterior a --to")
    try:
        with tempfile.TemporaryDirectory(prefix="aegis-report-") as directory:
            if args.adb:
                database = Path(directory) / "metrics.db"
                with database.open("wb") as output:
                    subprocess.run(remote_command(BACKUP), stdout=output, stderr=subprocess.DEVNULL,
                                   check=True, timeout=120)
                label = "Moto E7 Plus via ADB (backup online SQLite)"
            else:
                database = args.db or LOCAL_DB
                label = "arquivo SQLite local"
            cycles, rows = read_metrics(database, args.start, args.end)
        executive, page = build_report(cycles, rows, label, args.start, args.end)
        args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write(args.output_dir / "resumo_executivo.md", executive)
        atomic_write(args.output_dir / "relatorio.html", page)
    except (OSError, sqlite3.Error, ValueError, OverflowError, subprocess.SubprocessError) as error:
        print(f"Falha ao gerar relatório ({type(error).__name__}). Verifique a origem e o destino.", file=sys.stderr)
        return 1
    print(args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
