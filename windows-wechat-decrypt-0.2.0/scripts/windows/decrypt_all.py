#!/usr/bin/env python3
"""Verify a saved Windows raw key and decrypt the matching WeChat account."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[2]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts.windows.windows_crypto import (  # noqa: E402
    SQLITE_HEADER,
    decrypt_database,
    key_fingerprint,
    parse_raw_key,
    read_page_one,
    validate_plaintext_database,
    verify_database,
    verify_all_pages,
)


@dataclass(frozen=True)
class AccountCandidate:
    name: str
    db_storage: Path
    probe: Path


def default_data_roots() -> list[Path]:
    roots = [Path(r"D:\xwechat_files")]
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        roots.append(Path(user_profile) / "Documents" / "xwechat_files")
    return roots


def discover_accounts(data_roots: list[Path]) -> list[AccountCandidate]:
    accounts: list[AccountCandidate] = []
    seen: set[Path] = set()
    for root in data_roots:
        if not root.is_dir():
            continue
        for db_storage in root.glob("*/db_storage"):
            resolved = db_storage.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            probes = [
                db_storage / "message" / "message_0.db",
                db_storage / "session" / "session.db",
                db_storage / "contact" / "contact.db",
            ]
            probe = next((path for path in probes if path.is_file()), None)
            if probe:
                accounts.append(AccountCandidate(db_storage.parent.name, db_storage, probe))
    return sorted(accounts, key=lambda item: item.name)


def select_account(
    accounts: list[AccountCandidate], raw_key: bytes, requested: str | None
) -> AccountCandidate:
    if requested:
        candidates = [item for item in accounts if item.name == requested or item.name.startswith(requested)]
        if len(candidates) != 1:
            names = ", ".join(item.name for item in candidates) or "none"
            raise RuntimeError(f"account selector matched {len(candidates)} accounts: {names}")
        ok, reason = verify_database(raw_key, candidates[0].probe)
        if not ok:
            raise RuntimeError(f"saved key does not match {candidates[0].name}: {reason}")
        return candidates[0]

    matching = [item for item in accounts if verify_database(raw_key, item.probe)[0]]
    if len(matching) == 1:
        return matching[0]
    if not matching:
        raise RuntimeError("saved key did not match any discovered account; specify --account or extract a new key")
    raise RuntimeError("saved key matched multiple accounts; specify --account explicitly")


def load_raw_key(path: Path) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(f"key file not found: {path}")
    return parse_raw_key(path.read_text(encoding="ascii"))


def iter_encrypted_databases(db_storage: Path) -> list[Path]:
    result = []
    for path in db_storage.rglob("*.db"):
        try:
            if not read_page_one(path).startswith(SQLITE_HEADER):
                result.append(path)
        except OSError:
            continue
    return sorted(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Windows WeChat 4.x key verification and decryption")
    parser.add_argument("--key-file", type=Path, default=SKILL_DIR / "key_windows.txt")
    parser.add_argument("--data-root", type=Path, action="append", help="xwechat_files root; repeatable")
    parser.add_argument("--account", help="exact account directory name or unique prefix")
    parser.add_argument("--output", type=Path, default=SKILL_DIR / "decrypted")
    parser.add_argument("--verify-only", action="store_true", help="verify every encrypted DB without writing plaintext")
    parser.add_argument("--no-quick-check", action="store_true", help="skip SQLite PRAGMA quick_check after decryption")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        raw_key = load_raw_key(args.key_file)
        roots = args.data_root or default_data_roots()
        accounts = discover_accounts(roots)
        if not accounts:
            raise RuntimeError("no WeChat account directories found under the configured data roots")
        account = select_account(accounts, raw_key, args.account)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"key fingerprint: {key_fingerprint(raw_key)}")
    print(f"account: {account.name}")
    print(f"source: {account.db_storage}")
    databases = iter_encrypted_databases(account.db_storage)
    print(f"encrypted databases: {len(databases)}")

    verified = 0
    decrypted = 0
    failed: list[tuple[str, str]] = []
    destination_root = args.output / account.name / "db_storage"
    for source in databases:
        relative = source.relative_to(account.db_storage)
        if args.verify_only:
            ok, reason, pages = verify_all_pages(raw_key, source)
        else:
            ok, reason = verify_database(raw_key, source)
            pages = 0
        if not ok:
            failed.append((str(relative), reason))
            print(f"FAIL {relative}: {reason}")
            continue
        verified += 1
        if args.verify_only:
            print(f"OK   {relative} ({pages} pages)")
            continue
        destination = destination_root / relative
        try:
            pages = decrypt_database(raw_key, source, destination)
            if not args.no_quick_check:
                valid, detail = validate_plaintext_database(destination)
                if not valid:
                    raise RuntimeError(f"SQLite quick_check failed: {detail}")
            decrypted += 1
            suffix = f"; {detail}" if not args.no_quick_check and detail != "ok" else ""
            print(f"OK   {relative} ({pages} pages{suffix})")
        except Exception as exc:
            failed.append((str(relative), str(exc)))
            print(f"FAIL {relative}: {exc}")

    action_count = verified if args.verify_only else decrypted
    action = "verified" if args.verify_only else "decrypted"
    print(f"SUMMARY: {action_count} {action}, {len(failed)} failed, {len(databases)} total")
    if not args.verify_only:
        print(f"output: {destination_root}")
    return 0 if not failed and action_count == len(databases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
