"""Assinatura e verificação HMAC-SHA256 com comparação em tempo constante e anti-replay."""

from __future__ import annotations

import hashlib
import hmac
import time


def compute_hmac_sha256(secret: str, payload: bytes) -> str:
    """Calcula assinatura HMAC-SHA256 hexadecimal minúscula."""
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_signature(
    secret: str,
    payload: bytes,
    signature_header: str,
    payload_timestamp: int | None = None,
    now_ts: int | None = None,
    replay_window_sec: int = 300,
) -> tuple[bool, str]:
    """Valida assinatura HMAC-SHA256 (`sha256=<hex>`) e janela temporal contra replay."""
    if not secret:
        return False, "hmac_secret_not_configured"
    if not signature_header:
        return False, "missing_signature"

    provided = signature_header.strip()
    if provided.startswith("sha256="):
        provided = provided[7:]
    provided = provided.lower()

    if len(provided) != 64:
        return False, "malformed_signature"

    expected = compute_hmac_sha256(secret, payload)
    if not hmac.compare_digest(expected, provided):
        return False, "invalid_signature"

    if payload_timestamp is not None:
        current = int(time.time()) if now_ts is None else int(now_ts)
        if abs(current - int(payload_timestamp)) > replay_window_sec:
            return False, "timestamp_out_of_window"

    return True, "ok"
