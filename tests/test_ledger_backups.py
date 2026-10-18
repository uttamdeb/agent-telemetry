import copy
import datetime
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ledger_backups as B


def ledger(version=58, archived=True, marker="fixture"):
    return {"version": version, "files": {"deleted-source.jsonl": {
        "source": "codex", "records": {}, "archived": archived,
        "sessions": [{"id": marker, "tokens": 12}]}}}


class LedgerBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / ".usage_cache.json"

    def write_ledger(self, value=None, **kwargs):
        raw = json.dumps(value or ledger(**kwargs), separators=(",", ":")).encode()
        self.cache.write_bytes(raw)
        return raw

    def backup(self, **kwargs):
        return B.create_backup(self.cache, expected_version=58, **kwargs)

    def list_backups(self):
        return B.list_backups(self.cache, expected_version=58)

    def preview(self, backup_id):
        return B.preview_restore(self.cache, backup_id, expected_version=58)

    def restore(self, backup_id, expected_sha256, expected_current_sha256):
        return B.restore_backup(self.cache, backup_id, expected_sha256, expected_current_sha256,
                                expected_version=58)

    def test_scheduled_backup_throttles_once_per_utc_day_and_rotates(self):
        raw = self.write_ledger()
        first = self.backup(now=datetime.datetime(2026, 10, 16, 1, tzinfo=datetime.timezone.utc))
        self.assertEqual(Path(self.tmp.name, B.BACKUP_DIR_NAME, first["id"]).read_bytes(), raw)
        self.assertIsNone(self.backup(now=datetime.datetime(2026, 10, 16, 23, tzinfo=datetime.timezone.utc)))
        for day in range(17, 25):
            self.backup(force=True, now=datetime.datetime(2026, 10, day, 1, tzinfo=datetime.timezone.utc))
        scheduled = [x for x in self.list_backups() if x["reason"] == "scheduled"]
        self.assertEqual(len(scheduled), 7)
        self.assertEqual(scheduled[0]["files"], 1)
        self.assertEqual(scheduled[0]["sessions"], 1)

    def test_snapshot_keeps_archived_history_byte_for_byte(self):
        raw = self.write_ledger()
        item = self.backup()
        self.assertEqual((Path(self.tmp.name, B.BACKUP_DIR_NAME) / item["id"]).read_bytes(), raw)
        restored = self.preview(item["id"])
        self.assertEqual(restored["summary"]["archived"], 1)
        self.assertEqual(json.loads((Path(self.tmp.name, B.BACKUP_DIR_NAME) / item["id"]).read_bytes()), ledger())

    def test_invalid_json_and_unsupported_versions_are_not_restore_candidates(self):
        self.cache.write_bytes(b"{broken")
        safeguard = self.backup()
        self.assertEqual(safeguard["reason"], "safeguard")
        self.assertFalse(safeguard["valid"])
        with self.assertRaises(B.BackupError):
            self.preview(safeguard["id"])
        self.write_ledger(version=999)
        rejected = self.backup(reason="manual")
        self.assertEqual(rejected["reason"], "safeguard")
        self.assertFalse(rejected["valid"])

    def test_bad_ids_and_symlinks_are_refused(self):
        self.write_ledger()
        with self.assertRaises(B.BackupError):
            self.preview("..\\.usage_cache.json")
        item = self.backup()
        directory = Path(self.tmp.name, B.BACKUP_DIR_NAME)
        path = directory / item["id"]
        moved = directory / "real.json"
        path.rename(moved)
        try:
            path.symlink_to(moved)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this platform")
        with self.assertRaises(B.BackupError):
            self.preview(item["id"])

    def test_list_skips_filename_with_impossible_timestamp(self):
        self.write_ledger()
        directory = Path(self.tmp.name, B.BACKUP_DIR_NAME)
        directory.mkdir()
        malformed = directory / ("20261399T256199Z-" + "a" * 32 + "-manual.json")
        malformed.write_text("{}", encoding="utf-8")
        self.assertEqual(self.list_backups(), [])

    def test_list_orders_same_second_by_file_mtime(self):
        self.write_ledger(marker="first")
        instant = datetime.datetime(2026, 10, 16, 1, tzinfo=datetime.timezone.utc)
        first = self.backup(reason="manual", now=instant)
        self.write_ledger(marker="second")
        second = self.backup(reason="manual", now=instant)
        directory = Path(self.tmp.name, B.BACKUP_DIR_NAME)
        os.utime(directory / first["id"], ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
        os.utime(directory / second["id"], ns=(1_700_000_001_000_000_000, 1_700_000_001_000_000_000))
        self.assertEqual([x["id"] for x in self.list_backups()], [second["id"], first["id"]])

    def test_preview_rejects_missing_source_and_invalid_usage_values(self):
        malformed = ledger()
        del malformed["files"]["deleted-source.jsonl"]["source"]
        self.write_ledger(malformed)
        item = self.backup(reason="manual")
        self.assertFalse(item["valid"])
        with self.assertRaises(B.BackupError):
            self.preview(item["id"])

        malformed = ledger()
        malformed["files"]["deleted-source.jsonl"]["records"] = {"2026-10-16\tmodel": {"in": True}}
        self.write_ledger(malformed)
        item = self.backup(reason="manual")
        self.assertFalse(item["valid"])

    def test_atomic_restore_failure_leaves_original_ledger_unchanged(self):
        original = self.write_ledger(marker="original")
        item = self.backup(reason="manual")
        self.write_ledger(marker="newer")
        newer = self.cache.read_bytes()
        preview = self.preview(item["id"])
        real_replace = os.replace

        def fail_cache_replace(source, destination):
            if Path(destination) == self.cache:
                raise OSError("fixture replace failure")
            return real_replace(source, destination)

        with patch.object(B.os, "replace", side_effect=fail_cache_replace):
            with self.assertRaises(OSError):
                self.restore(item["id"], preview["sha256"], preview["current_sha256"])
        self.assertEqual(self.cache.read_bytes(), newer)
        self.assertNotEqual(self.cache.read_bytes(), original)

    def test_malformed_session_records_and_precision_rows_cannot_replace_ledger(self):
        valid = ledger()
        bad_sessions = [
            {"records": []},
            {"records": {"not-a-date\tmodel": {"in": 1}}},
            {"records": {"2026-10-16\tmodel": {"in": "100"}}},
            {"records": {"2026-10-16\tmodel": {"exact": []}}},
            {"records": {"2026-10-16\tmodel": {"exact": {"in": True}}}},
            {"days": []},
            {"days": {"2026-10-16": {"out": float("nan")}}},
        ]
        for fields in bad_sessions:
            with self.subTest(fields=fields):
                malformed = copy.deepcopy(valid)
                malformed["files"]["deleted-source.jsonl"]["sessions"][0].update(fields)
                rejected_bytes = self.write_ledger(malformed)
                item = self.backup(reason="manual")
                self.assertFalse(item["valid"])
                self.assertEqual((self.cache.parent / B.BACKUP_DIR_NAME / item["id"]).read_bytes(), rejected_bytes)
                current = self.write_ledger(marker="keep-current")
                with self.assertRaises(B.BackupError):
                    self.preview(item["id"])
                self.assertEqual(self.cache.read_bytes(), current)

    def test_old_optional_and_nested_precision_usage_remains_restorable(self):
        for version in range(52, 59):
            with self.subTest(version=version):
                value = ledger(version=version)
                value["files"]["deleted-source.jsonl"]["sessions"][0].update(
                    records={"2026-10-16\tmodel": {"in": 100, "exact": {"in": 80}}},
                    days={"2026-10-16": {"in": 100}})
                raw = self.write_ledger(value)
                item = self.backup(reason="manual")
                self.assertTrue(item["valid"])
                self.assertEqual(self.preview(item["id"])["sha256"], item["sha256"])
                self.assertEqual((self.cache.parent / B.BACKUP_DIR_NAME / item["id"]).read_bytes(), raw)

    def test_malformed_aggregate_containers_are_safeguards_only(self):
        for name in ("state", "totals", "tools", "skills", "ctx", "hourly", "activity",
                     "reads", "used_ext", "mcp_offered", "compactions"):
            with self.subTest(name=name):
                malformed = ledger()
                malformed["files"]["deleted-source.jsonl"][name] = []
                self.write_ledger(malformed)
                item = self.backup(reason="manual")
                self.assertFalse(item["valid"])
                with self.assertRaises(B.BackupError):
                    self.preview(item["id"])

    def test_changed_backup_or_ledger_makes_preview_stale(self):
        self.write_ledger(marker="older")
        item = self.backup(reason="manual")
        preview = self.preview(item["id"])
        self.write_ledger(marker="edited")
        with self.assertRaisesRegex(B.BackupError, "ledger changed"):
            self.restore(item["id"], preview["sha256"], preview["current_sha256"])
        self.write_ledger(marker="older")
        preview = self.preview(item["id"])
        backup_path = Path(self.tmp.name, B.BACKUP_DIR_NAME, item["id"])
        backup_path.write_bytes(json.dumps(ledger(marker="tampered")).encode())
        with self.assertRaisesRegex(B.BackupError, "backup changed"):
            self.restore(item["id"], preview["sha256"], preview["current_sha256"])

    def test_restore_preserves_current_ledger_before_replacement(self):
        before = self.write_ledger(marker="old")
        candidate = self.backup(reason="manual")
        current = self.write_ledger(marker="current")
        preview = self.preview(candidate["id"])
        result = self.restore(candidate["id"], preview["sha256"], preview["current_sha256"])
        self.assertTrue(result["ok"])
        self.assertEqual(self.cache.read_bytes(), before)
        snapshots = self.list_backups()
        safety = next(x for x in snapshots if x["reason"] == "pre-restore")
        self.assertEqual((Path(self.tmp.name, B.BACKUP_DIR_NAME) / safety["id"]).read_bytes(), current)

    def test_private_modes_on_posix(self):
        if os.name == "nt":
            self.skipTest("POSIX mode bits are not enforced on Windows")
        self.write_ledger()
        item = self.backup()
        self.assertEqual((Path(self.tmp.name, B.BACKUP_DIR_NAME).stat().st_mode & 0o777), 0o700)
        self.assertEqual((Path(self.tmp.name, B.BACKUP_DIR_NAME) / item["id"]).stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
