#!/usr/bin/env python3
"""Capture the Windows account-wide raw key at the PBKDF2/SHA-512 boundary."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass

SKILL_DIR = Path(__file__).resolve().parents[2]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts.windows.decrypt_all import (  # noqa: E402
    AccountCandidate,
    default_data_roots,
    discover_accounts,
    load_raw_key,
    select_account,
)
from scripts.windows.windows_crypto import key_fingerprint, verify_raw_key  # noqa: E402


FRIDA_SCRIPT = r"""
setImmediate(function() {
function waitModule(name, timeoutMs) {
  var started = Date.now();
  while (Date.now() - started < timeoutMs) {
    var module = Process.findModuleByName(name);
    if (module) return module;
    Thread.sleep(0.02);
  }
  return null;
}

var module = waitModule("Weixin.dll", 30000);
if (!module) { send("ERROR:Weixin.dll was not loaded"); throw new Error("module timeout"); }

var constants = Memory.scanSync(module.base, module.size, "22 ae 28 d7 98 2f 8a 42");
if (!constants.length) { send("ERROR:SHA512 constant table not found"); throw new Error("constant table"); }
var table = constants[0].address;
var reference = null;
["48 8d", "4c 8d"].forEach(function(opcode) {
  ["05", "0d", "15", "1d", "25", "2d", "35", "3d"].forEach(function(modrm) {
    if (reference) return;
    try {
      Memory.scanSync(module.base, module.size, opcode + " " + modrm).forEach(function(hit) {
        if (reference) return;
        var instruction = hit.address;
        var displacement = instruction.add(3).readS32();
        if (instruction.add(7).add(displacement).equals(table)) reference = instruction;
      });
    } catch (error) {}
  });
});
if (!reference) { send("ERROR:SHA512 table reference not found"); throw new Error("reference"); }

var entry = null;
for (var offset = 0; offset < 8192; offset++) {
  var address = reference.sub(offset);
  try {
    if (new Uint8Array(address.sub(1).readByteArray(1))[0] === 0xcc) {
      entry = address;
      break;
    }
  } catch (error) {}
}
if (!entry) { send("ERROR:SHA512 function entry not found"); throw new Error("entry"); }

function recoverIpad(pointer) {
  try {
    var block = new Uint8Array(pointer.readByteArray(128));
    for (var i = 32; i < 128; i++) if (block[i] !== 0x36) return null;
    var result = "";
    for (var j = 0; j < 32; j++) result += ("0" + (block[j] ^ 0x36).toString(16)).slice(-2);
    return result;
  } catch (error) {
    return null;
  }
}

var seen = {};
Interceptor.attach(entry, {
  onEnter: function() {
    var key = recoverIpad(this.context.rdx);
    if (key && !seen[key]) {
      seen[key] = true;
      send("KEY:" + key);
    }
  }
});
send("READY:" + entry.sub(module.base));
});
"""


def main_process_id() -> int | None:
    command = (
        "Get-Process Weixin -ErrorAction SilentlyContinue | "
        "Where-Object {$_.WorkingSet64 -gt 20MB} | "
        "Sort-Object WorkingSet64 -Descending | "
        "Select-Object -First 1 -ExpandProperty Id"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    value = result.stdout.strip()
    return int(value) if value.isdigit() else None


def find_weixin_executable(requested: Path | None) -> Path:
    if requested:
        if requested.is_file():
            return requested
        raise FileNotFoundError(f"Weixin executable not found: {requested}")
    command = (
        "Get-Process Weixin -ErrorAction SilentlyContinue | "
        "Sort-Object WorkingSet64 -Descending | "
        "Select-Object -First 1 -ExpandProperty Path"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    candidates = []
    if result.stdout.strip():
        candidates.append(Path(result.stdout.strip()))
    candidates.append(Path(r"D:\0_soft\Weixin\Weixin.exe"))
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(variable)
        if base:
            candidates.extend(
                [
                    Path(base) / "Tencent" / "Weixin" / "Weixin.exe",
                    Path(base) / "Tencent" / "WeChat" / "WeChat.exe",
                ]
            )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Weixin.exe was not found; pass --weixin-exe")


def choose_account(accounts: list[AccountCandidate], requested: str | None) -> AccountCandidate:
    if requested:
        matches = [item for item in accounts if item.name == requested or item.name.startswith(requested)]
        if len(matches) != 1:
            raise RuntimeError(f"--account matched {len(matches)} accounts")
        return matches[0]
    if len(accounts) == 1:
        return accounts[0]
    names = ", ".join(item.name for item in accounts)
    raise RuntimeError(f"multiple accounts found; pass --account. Available: {names}")


def save_key(path: Path, raw_key_hex: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(raw_key_hex + "\n", encoding="ascii")
    os.replace(temporary, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Windows WeChat 4.x raw key extractor")
    parser.add_argument("--seconds", type=int, default=120, help="maximum capture time")
    parser.add_argument("--data-root", type=Path, action="append", help="xwechat_files root; repeatable")
    parser.add_argument("--account", help="exact account directory name or unique prefix")
    parser.add_argument("--output", type=Path, default=SKILL_DIR / "key_windows.txt")
    parser.add_argument("--mode", choices=("spawn", "manual"), default="spawn")
    parser.add_argument("--weixin-exe", type=Path, help="Weixin.exe path for spawn mode")
    parser.add_argument("--force", action="store_true", help="kill/restart WeChat and capture a new key")
    parser.add_argument("--yes", action="store_true", help="skip the destructive restart confirmation")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    accounts = discover_accounts(args.data_root or default_data_roots())
    if not accounts:
        print("ERROR: no WeChat account database found", file=sys.stderr)
        return 2

    if args.account:
        try:
            choose_account(accounts, args.account)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    if args.output.is_file():
        try:
            saved = load_raw_key(args.output)
            account = select_account(accounts, saved, args.account)
            print(f"saved key is valid for account: {account.name}")
            print(f"key fingerprint: {key_fingerprint(saved)}")
            if not args.force:
                print("Hook is not required. Run decrypt_all.py to decrypt databases.")
                return 0
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"saved key is not reusable: {exc}")

    if not args.force:
        print("No reusable key was found. Re-run with --force to capture a new key.", file=sys.stderr)
        return 10
    try:
        account = choose_account(accounts, args.account)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not args.yes:
        prompt = "This will close and restart WeChat. Continue? [y/N] "
        answer = input(prompt)
        if answer.strip().lower() not in {"y", "yes"}:
            print("Cancelled.")
            return 1

    page_one = account.probe.read_bytes()[:4096]
    print(f"verification account: {account.name}")
    print(f"verification database: {account.probe}")
    try:
        import frida
    except ImportError:
        print("ERROR: frida is not installed; run setup_windows.ps1", file=sys.stderr)
        return 4

    try:
        executable = find_weixin_executable(args.weixin_exe) if args.mode == "spawn" else None
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    subprocess.run(["taskkill", "/F", "/IM", "Weixin.exe"], capture_output=True, check=False)
    for _ in range(100):
        if main_process_id() is None:
            break
        time.sleep(0.1)

    deadline = time.monotonic() + args.seconds
    device = frida.get_local_device()
    captured: dict[str, str] = {}
    ready = False

    def on_message(message, _data) -> None:
        nonlocal ready
        if message.get("type") != "send":
            return
        payload = message.get("payload", "")
        if not isinstance(payload, str):
            return
        if payload.startswith("READY:"):
            ready = True
            print("SHA-512 hook installed; waiting for database key derivation...")
            return
        if payload.startswith("ERROR:"):
            print(payload, file=sys.stderr)
            return
        if not payload.startswith("KEY:"):
            return
        key_hex = payload[4:]
        if key_hex in captured:
            return
        try:
            raw_key = bytes.fromhex(key_hex)
        except ValueError:
            return
        if len(raw_key) == 32 and verify_raw_key(raw_key, page_one):
            captured[key_hex] = "raw"
            save_key(args.output, key_hex)
            print(f"raw key captured and verified; fingerprint: {key_fingerprint(raw_key)}")

    try:
        if args.mode == "spawn":
            print(f"spawning and instrumenting: {executable}")
            process_id = device.spawn([str(executable)])
            session = device.attach(process_id)
            script = session.create_script(FRIDA_SCRIPT)
            script.on("message", on_message)
            script.load()
            device.resume(process_id)
        else:
            print("Restart WeChat by double-clicking it on the Windows desktop.")
            print("Waiting for the main process...")
            process_id = None
            while time.monotonic() < deadline:
                process_id = main_process_id()
                if process_id:
                    break
                time.sleep(0.04)
            if not process_id:
                print("ERROR: WeChat main process was not detected", file=sys.stderr)
                return 3
            print(f"attaching to main process: {process_id}")
            session = device.attach(process_id)
            script = session.create_script(FRIDA_SCRIPT)
            script.on("message", on_message)
            script.load()
    except Exception as exc:
        print(f"ERROR: Frida startup failed: {exc}", file=sys.stderr)
        return 4

    while time.monotonic() < deadline and not captured:
        time.sleep(0.25)
    try:
        script.unload()
        session.detach()
    except Exception:
        pass

    if captured:
        print(f"key saved to: {args.output}")
        return 0
    detail = "hook was installed" if ready else "hook did not become ready"
    print(f"ERROR: no valid raw key captured ({detail})", file=sys.stderr)
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
