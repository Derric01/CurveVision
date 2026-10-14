#!/usr/bin/env python3
"""Minisign keys and signatures, as Tauri's updater reads them — for tests only.

    python scripts/minisign.py      # print a test vector for the shell's Rust test

Tauri's updater verifies a download against a minisign public key built into the
application. The release key is the maintainers' and is never in this repository; the
harnesses that check the updater refuses what it should (`verify_desktop_shell.py`) make a
throwaway key pair here instead, written from minisign's published format:

* a public key is two lines — an untrusted comment, and base64 of ``"Ed"``, an eight-byte key
  ID and the 32-byte Ed25519 public key;
* a signature is four — an untrusted comment; base64 of ``"ED"`` (the BLAKE2b-512 prehashed
  algorithm), the key ID and the Ed25519 signature of the file's hash; the trusted comment;
  and base64 of the signature of the first signature followed by the trusted comment;
* Tauri wants each file's whole text base64-encoded again.

The Rust test ``a_python_made_signature_verifies`` pins this against the ``minisign-verify``
crate the updater itself uses, so a signature the harness makes is known to be well formed.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import sys
from dataclasses import dataclass


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@dataclass(frozen=True)
class KeyPair:
    secret: object  # cryptography's Ed25519PrivateKey
    key_id: bytes

    @property
    def tauri_pubkey(self) -> str:
        """The public key as Tauri's configuration takes it."""
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        public = self.secret.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)  # type: ignore[attr-defined]
        text = (
            f"untrusted comment: minisign public key: {self.key_id[::-1].hex().upper()}\n"
            f"{_b64(b'Ed' + self.key_id + public)}\n"
        )
        return _b64(text.encode())

    def tauri_signature(self, data: bytes, *, trusted: str = "timestamp:0\tfile:update") -> str:
        """``data``'s signature as a Tauri update feed carries it."""
        signature = self.secret.sign(hashlib.blake2b(data, digest_size=64).digest())  # type: ignore[attr-defined]
        global_signature = self.secret.sign(signature + trusted.encode())  # type: ignore[attr-defined]
        text = (
            "untrusted comment: signature from a CurveVision test key\n"
            f"{_b64(b'ED' + self.key_id + signature)}\n"
            f"trusted comment: {trusted}\n"
            f"{_b64(global_signature)}\n"
        )
        return _b64(text.encode())


def generate() -> KeyPair:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return KeyPair(Ed25519PrivateKey.generate(), secrets.token_bytes(8))


def main() -> int:
    keys = generate()
    data = b"CurveVision update test vector"
    print(f'const PUBKEY: &str = "{keys.tauri_pubkey}";')
    print(f'const SIGNATURE: &str = "{keys.tauri_signature(data)}";')
    print(f"const DATA: &[u8] = b{data.decode()!r};".replace("b'", 'b"').replace("';", '";'))
    return 0


if __name__ == "__main__":
    sys.exit(main())
