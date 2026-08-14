import hashlib
import hmac
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'refresh_decrypt.py'
PAGE_SIZE = 4096


def make_page(key: bytes, valid: bool) -> bytes:
    page = bytearray(os.urandom(PAGE_SIZE))
    salt = bytes(page[:16])
    mac_salt = bytes(value ^ 0x3A for value in salt)
    mac_key = hashlib.pbkdf2_hmac('sha512', key, mac_salt, 2, dklen=32)
    digest = hmac.new(mac_key, page[16:4032], hashlib.sha512)
    digest.update(struct.pack('<I', 1))
    page[-64:] = digest.digest() if valid else b'\x00' * 64
    return bytes(page)


class RefreshDecryptTest(unittest.TestCase):
    def run_refresh(self, valid_required=True, include_optional=False):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        db_dir = root / 'encrypted'
        out_dir = root / 'decrypted'
        message_dir = db_dir / 'message'
        message_dir.mkdir(parents=True)

        key = bytes(range(32))
        keys = {}

        required_rel = 'message/message_0.db'
        (db_dir / required_rel).write_bytes(make_page(key, valid_required))
        keys[required_rel] = {'enc_key': key.hex()}

        if include_optional:
            optional_rel = 'message/message_resource.db'
            (db_dir / optional_rel).write_bytes(make_page(key, False))
            keys[optional_rel] = {'enc_key': key.hex()}

        keys_path = root / 'all_keys.json'
        keys_path.write_text(json.dumps(keys))
        config_path = root / 'config.yaml'
        config_path.write_text(
            'wechat:\n'
            f'  db_dir: "{db_dir}"\n'
            f'  decrypted_dir: "{out_dir}"\n'
            f'  keys_file: "{keys_path}"\n'
            f'  collector_db: "{root / "collector.db"}"\n'
        )

        proc = subprocess.run(
            [sys.executable, str(SCRIPT), '--config', str(config_path)],
            text=True,
            capture_output=True,
        )
        state_path = out_dir / '.refresh_state.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        return proc, state, config_path

    def test_required_hmac_failure_is_retried(self):
        first, state, config_path = self.run_refresh(valid_required=False)
        self.assertEqual(2, first.returncode)
        self.assertNotIn('message/message_0.db', state)
        second = subprocess.run(
            [sys.executable, str(SCRIPT), '--config', str(config_path)],
            text=True,
            capture_output=True,
        )
        self.assertEqual(2, second.returncode)
        self.assertIn('HMAC 验证失败 message/message_0.db', second.stderr)

    def test_optional_resource_failure_does_not_block_text_refresh(self):
        proc, state, _ = self.run_refresh(valid_required=True, include_optional=True)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn('message/message_0.db', state)
        self.assertNotIn('message/message_resource.db', state)
        self.assertIn('文本消息同步可继续', proc.stderr)


if __name__ == '__main__':
    unittest.main()
