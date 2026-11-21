"""Testes de compatibilidade bit-a-bit do HMAC-SHA256 POSIX (Android) vs Python stdlib."""

from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from netsentinel_hub.security import compute_hmac_sha256, verify_signature

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_COMMON = REPO_ROOT / "node_android" / "netsentinel" / "lib_common.sh"


def _run_posix_hmac(secret: str, message: str, force_no_openssl: bool) -> str:
    env = os.environ.copy()
    if force_no_openssl:
        # Cria PATH isolado sem openssl para forçar o caminho sha256sum + xxd + awk usado no Android
        bin_dir = Path("/tmp/netsentinel_test_no_openssl_bin")
        bin_dir.mkdir(parents=True, exist_ok=True)
        for tool in ("sh", "sha256sum", "xxd", "awk", "tr", "printf", "cut", "date", "mkdir", "dirname"):
            found = shutil.which(tool)
            if found:
                link = bin_dir / tool
                if not link.exists():
                    link.symlink_to(found)
        env["PATH"] = str(bin_dir)

    script = f'. "{LIB_COMMON}"\nhmac_sha256_hex "$1" "$2"\n'
    res = subprocess.run(
        ["/bin/sh", "-c", script, "test_hmac", secret, message],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


@pytest.mark.parametrize(
    "secret,message",
    [
        ("super-secret-key", '{"node_id":"e7plus-jp-ap","ac_online":true,"battery_pct":72}'),
        ("a" * 64, "exact-64-byte-key-test"),
        ("x" * 96, "key-longer-than-sha256-block-size-must-be-hashed-first"),
        ("0123456789abcdef" * 4, '{"location":"Apê João Pessoa","temp_c":33.5}'),
        ("short", ""),
    ],
)
def test_posix_hmac_matches_python_rfc2104(secret: str, message: str) -> None:
    expected = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()

    # Caminho com openssl (Linux host)
    with_openssl = _run_posix_hmac(secret, message, force_no_openssl=False)
    assert with_openssl == expected

    # Caminho puro sha256sum + xxd + awk (exatamente o que roda no Moto E7 Plus)
    without_openssl = _run_posix_hmac(secret, message, force_no_openssl=True)
    assert without_openssl == expected
    assert compute_hmac_sha256(secret, message.encode("utf-8")) == expected


def test_verify_signature_replay_and_tamper_protection() -> None:
    secret = "c0ffee1234567890abcdef"
    payload = b'{"node_id":"e7plus-jp-ap","timestamp":1700000000}'
    sig = f"sha256={compute_hmac_sha256(secret, payload)}"

    ok, reason = verify_signature(secret, payload, sig, payload_timestamp=1700000000, now_ts=1700000100)
    assert ok is True
    assert reason == "ok"

    # Tampering no payload
    ok, reason = verify_signature(secret, payload + b" ", sig, payload_timestamp=1700000000, now_ts=1700000100)
    assert ok is False
    assert reason == "invalid_signature"

    # Replay fora da janela de 300s
    ok, reason = verify_signature(secret, payload, sig, payload_timestamp=1700000000, now_ts=1700000401)
    assert ok is False
    assert reason == "timestamp_out_of_window"

    # Assinatura ausente ou malformada
    ok, reason = verify_signature(secret, payload, "", payload_timestamp=1700000000, now_ts=1700000000)
    assert ok is False
    assert reason == "missing_signature"
