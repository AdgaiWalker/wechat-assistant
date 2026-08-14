#!/usr/bin/env python3
"""Windows WeChat 4.x SQLCipher-compatible page crypto helpers."""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import struct
from pathlib import Path

from scripts.common.crypto_backend import aes_cbc_decrypt

PAGE_SIZE = 4096
SALT_SIZE = 16
IV_SIZE = 16
HMAC_SIZE = 64
RESERVE_SIZE = IV_SIZE + HMAC_SIZE
PAYLOAD_END = PAGE_SIZE - RESERVE_SIZE
SQLITE_HEADER = b"SQLite format 3\x00"


class CryptoFormatError(ValueError):
    """The input is not a supported encrypted WeChat database."""


def parse_raw_key(raw_key_hex: str) -> bytes:
    value = raw_key_hex.strip()
    if len(value) != 64:
        raise ValueError("raw key must be exactly 64 hexadecimal characters")
    try:
        key = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError("raw key contains non-hexadecimal characters") from exc
    if len(key) != 32:
        raise ValueError("raw key must decode to 32 bytes")
    return key


def key_fingerprint(raw_key: bytes) -> str:
    return hashlib.sha256(raw_key).hexdigest()[:16]


def derive_encryption_key(raw_key: bytes, salt: bytes) -> bytes:
    if len(raw_key) != 32 or len(salt) != SALT_SIZE:
        raise ValueError("raw key must be 32 bytes and salt must be 16 bytes")
    return hashlib.pbkdf2_hmac("sha512", raw_key, salt, 256000, dklen=32)


def derive_mac_key(encryption_key: bytes, salt: bytes) -> bytes:
    mac_salt = bytes(value ^ 0x3A for value in salt)
    return hashlib.pbkdf2_hmac("sha512", encryption_key, mac_salt, 2, dklen=32)


def verify_page(page: bytes, page_number: int, mac_key: bytes) -> bool:
    if len(page) != PAGE_SIZE or page_number < 1:
        return False
    start = SALT_SIZE if page_number == 1 else 0
    digest = hmac.new(mac_key, page[start : PAYLOAD_END + IV_SIZE], hashlib.sha512)
    digest.update(struct.pack("<I", page_number))
    return hmac.compare_digest(digest.digest(), page[-HMAC_SIZE:])


def verify_raw_key(raw_key: bytes, page_one: bytes) -> bool:
    if len(page_one) != PAGE_SIZE or page_one.startswith(SQLITE_HEADER):
        return False
    salt = page_one[:SALT_SIZE]
    encryption_key = derive_encryption_key(raw_key, salt)
    mac_key = derive_mac_key(encryption_key, salt)
    return verify_page(page_one, 1, mac_key)


def read_page_one(path: os.PathLike[str] | str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(PAGE_SIZE)


def verify_database(raw_key: bytes, source: os.PathLike[str] | str) -> tuple[bool, str]:
    path = Path(source)
    try:
        size = path.stat().st_size
    except OSError as exc:
        return False, f"cannot stat file: {exc}"
    if size < PAGE_SIZE:
        return False, "file is smaller than one page"
    if size % PAGE_SIZE:
        return False, f"file size is not a multiple of {PAGE_SIZE}"
    page_one = read_page_one(path)
    if page_one.startswith(SQLITE_HEADER):
        return False, "database is already plaintext"
    if not verify_raw_key(raw_key, page_one):
        return False, "page 1 HMAC mismatch"
    return True, "ok"


def verify_all_pages(raw_key: bytes, source: os.PathLike[str] | str) -> tuple[bool, str, int]:
    path = Path(source)
    try:
        size = path.stat().st_size
    except OSError as exc:
        return False, f"cannot stat file: {exc}", 0
    if size < PAGE_SIZE or size % PAGE_SIZE:
        return False, f"invalid encrypted database size: {size}", 0
    with path.open("rb") as handle:
        page_one = handle.read(PAGE_SIZE)
        if page_one.startswith(SQLITE_HEADER):
            return False, "database is already plaintext", 0
        salt = page_one[:SALT_SIZE]
        encryption_key = derive_encryption_key(raw_key, salt)
        mac_key = derive_mac_key(encryption_key, salt)
        page_number = 1
        page = page_one
        while page:
            if len(page) != PAGE_SIZE:
                return False, f"short page {page_number}", page_number - 1
            if not verify_page(page, page_number, mac_key):
                return False, f"page {page_number} HMAC mismatch", page_number - 1
            page_number += 1
            page = handle.read(PAGE_SIZE)
    return True, "ok", page_number - 1


def _decrypt_page(page: bytes, page_number: int, encryption_key: bytes) -> bytes:
    start = SALT_SIZE if page_number == 1 else 0
    iv = page[PAYLOAD_END : PAYLOAD_END + IV_SIZE]
    plaintext = aes_cbc_decrypt(encryption_key, iv, page[start:PAYLOAD_END])
    prefix = SQLITE_HEADER if page_number == 1 else b""
    return prefix + plaintext + (b"\x00" * RESERVE_SIZE)


def decrypt_database(
    raw_key: bytes,
    source: os.PathLike[str] | str,
    destination: os.PathLike[str] | str,
) -> int:
    """Verify and decrypt a database atomically. Returns the page count."""
    source_path = Path(source)
    destination_path = Path(destination)
    size = source_path.stat().st_size
    if size < PAGE_SIZE or size % PAGE_SIZE:
        raise CryptoFormatError(f"invalid encrypted database size: {size}")

    with source_path.open("rb") as input_handle:
        page_one = input_handle.read(PAGE_SIZE)
        if page_one.startswith(SQLITE_HEADER):
            raise CryptoFormatError("source database is already plaintext")
        salt = page_one[:SALT_SIZE]
        encryption_key = derive_encryption_key(raw_key, salt)
        mac_key = derive_mac_key(encryption_key, salt)
        if not verify_page(page_one, 1, mac_key):
            raise CryptoFormatError("page 1 HMAC mismatch")

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = destination_path.with_name(destination_path.name + ".part")
        try:
            with temporary_path.open("wb") as output_handle:
                output_handle.write(_decrypt_page(page_one, 1, encryption_key))
                page_number = 2
                while True:
                    page = input_handle.read(PAGE_SIZE)
                    if not page:
                        break
                    if len(page) != PAGE_SIZE:
                        raise CryptoFormatError(f"short page {page_number}")
                    if not verify_page(page, page_number, mac_key):
                        raise CryptoFormatError(f"page {page_number} HMAC mismatch")
                    output_handle.write(_decrypt_page(page, page_number, encryption_key))
                    page_number += 1
                output_handle.flush()
                os.fsync(output_handle.fileno())
            os.replace(temporary_path, destination_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
    return size // PAGE_SIZE


def validate_plaintext_database(path: os.PathLike[str] | str) -> tuple[bool, str]:
    try:
        connection = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
        try:
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
            try:
                row = connection.execute("PRAGMA quick_check").fetchone()
            except sqlite3.OperationalError as exc:
                if "no such tokenizer" in str(exc).lower():
                    return True, f"schema readable; quick_check unavailable: {exc}"
                raise
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return False, str(exc)
    if not row or row[0] != "ok":
        return False, str(row[0] if row else "quick_check returned no result")
    return True, "ok"
