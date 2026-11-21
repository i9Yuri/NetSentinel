"""Módulo de análise forense e raio-X de dispositivos na rede local (stdlib pura)."""

from __future__ import annotations

import socket
import threading
from typing import Any

# Portas padrão de rede doméstica para fingerprinting não intrusivo
STANDARD_PROBE_PORTS = [
    (80, "HTTP", "Interface Web / Painel de Configuração"),
    (443, "HTTPS", "Interface Web Segura"),
    (554, "RTSP", "Câmera de Vigilância / Transmissão de Vídeo"),
    (445, "SMB", "Compartilhamento de Arquivos Windows / Samba"),
    (139, "NetBIOS", "Rede Local Windows / Compartilhamento"),
    (8008, "Google Cast", "Transmissão Multimídia / Smart TV"),
    (8080, "HTTP-Alt", "Servidor Web Alternativo / Câmera IP"),
    (9100, "JetDirect", "Impressora de Rede"),
    (22, "SSH", "Acesso Administrativo por Terminal"),
    (5000, "UPnP/Media", "Servidor de Mídia / IoT"),
]


def probe_single_port(ip: str, port: int, timeout_sec: float = 0.8) -> bool:
    """Tenta uma conexão TCP rápida de teste em uma porta específica."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout_sec)
            return s.connect_ex((ip, port)) == 0
    except (OSError, ValueError):
        return False


def probe_device_services(
    ip: str,
    ports: list[tuple[int, str, str]] | None = None,
    timeout_sec: float = 0.8,
) -> dict[str, Any]:
    """Sonda portas conhecidas em paralelo com threads leves (< 1 segundo total)."""
    target_ports = ports or STANDARD_PROBE_PORTS
    open_ports: list[dict[str, Any]] = []
    lock = threading.Lock()
    threads: list[threading.Thread] = []

    def _worker(p: int, srv: str, desc: str) -> None:
        if probe_single_port(ip, p, timeout_sec):
            with lock:
                open_ports.append({"port": p, "service": srv, "description": desc})

    for p, srv, desc in target_ports:
        t = threading.Thread(target=_worker, args=(p, srv, desc), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=timeout_sec + 0.3)

    open_ports.sort(key=lambda x: x["port"])
    port_nums = [item["port"] for item in open_ports]

    # Heurística preditiva de diagnóstico do aparelho
    if 554 in port_nums:
        verdict = "📹 Câmera de Vigilância / Gravador de Vídeo (Porta RTSP de vídeo aberta)"
        category_hint = "CAMERA_SECURITY"
    elif 8008 in port_nums or 8009 in port_nums:
        verdict = "📺 Smart TV / Chromecast / Dispositivo de Streaming (Google Cast ativo)"
        category_hint = "SMART_TV"
    elif 9100 in port_nums or 631 in port_nums:
        verdict = "🖨️ Impressora de Rede / Servidor de Impressão"
        category_hint = "PRINTER"
    elif 445 in port_nums or 139 in port_nums:
        verdict = "💻 Computador / Servidor de Arquivos (Serviço de rede Windows/Samba exposto)"
        category_hint = "COMPUTER"
    elif 80 in port_nums or 8080 in port_nums or 443 in port_nums:
        verdict = "🌐 Aparelho com Servidor Web (Roteador, Câmera IP, Tomada Inteligente ou IoT)"
        category_hint = "WEB_IOT"
    elif 22 in port_nums:
        verdict = "🖥️ Sistema Linux / Servidor com porta SSH aberta"
        category_hint = "SERVER"
    else:
        verdict = "📱 Dispositivo Pessoal Silencioso (Provável smartphone, tablet ou aparelho com firewall ativo)"
        category_hint = "SILENT_DEVICE"

    return {
        "ip": ip,
        "open_ports": open_ports,
        "verdict": verdict,
        "category_hint": category_hint,
    }
