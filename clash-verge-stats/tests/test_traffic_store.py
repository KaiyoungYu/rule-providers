import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from traffic_store import (
    clear_history,
    csv_safe_target,
    load_connection_settings,
    open_db,
    read_stats,
    record_sample,
    save_connection_settings,
    source_label,
)


def connection(connection_id, host, down, up):
    return {
        "id": connection_id,
        "start": "2026-09-30T00:00:00Z",
        "metadata": {"host": host},
        "download": down,
        "upload": up,
    }


class TrafficStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = open_db(Path(self.temp.name) / "test.sqlite3")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_multiple_connections_sum_only_new_bytes(self):
        record_sample(self.db, [connection("a", "example.com", 100, 10)], 120)
        self.assertEqual(read_stats(self.db, 120), [])
        record_sample(
            self.db,
            [connection("a", "example.com", 150, 20), connection("b", "example.com", 30, 5)],
            121,
        )
        self.assertEqual(read_stats(self.db, 121), [("example.com", 80, 15)])
        record_sample(self.db, [connection("a", "example.com", 150, 20)], 122)
        self.assertEqual(read_stats(self.db, 122), [("example.com", 80, 15)])

    def test_restart_gap_is_baselined_and_old_minutes_expire(self):
        record_sample(self.db, [connection("a", "example.com", 10, 0)], 120)
        record_sample(self.db, [connection("a", "example.com", 20, 0)], 121)
        record_sample(self.db, [connection("a", "example.com", 1000, 0)], 150)
        self.assertEqual(read_stats(self.db, 150), [("example.com", 10, 0)])
        record_sample(self.db, [connection("a", "example.com", 1010, 0)], 151)
        self.assertEqual(read_stats(self.db, 151), [("example.com", 20, 0)])
        self.assertEqual(read_stats(self.db, 120 + 24 * 60 * 60), [])

    def test_csv_target_cannot_start_a_formula(self):
        self.assertEqual(csv_safe_target("=1+1"), "'=1+1")
        self.assertEqual(csv_safe_target("\t@SUM(1)"), "'\t@SUM(1)")
        self.assertEqual(csv_safe_target("example.com"), "example.com")

    def test_source_label_does_not_show_url_credentials(self):
        missing_settings = Path(self.temp.name) / "missing.json"
        with patch("traffic_store.settings_path", return_value=missing_settings):
            with patch.dict("os.environ", {"CLASH_API_URL": "http://name:token@127.0.0.1:9097/connections"}):
                self.assertEqual(source_label(), "http://127.0.0.1:9097")

    def test_saved_page_settings_override_environment(self):
        settings_file = Path(self.temp.name) / "config/settings.json"
        with patch.dict("os.environ", {"CLASH_API_URL": "http://127.0.0.1:9090", "CLASH_SECRET": "old"}):
            save_connection_settings("http://127.0.0.1:9097", "new-secret", settings_file)
            self.assertEqual(load_connection_settings(settings_file), ("http://127.0.0.1:9097", "new-secret"))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(settings_file.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(settings_file.parent.stat().st_mode), 0o700)

    def test_changing_source_starts_from_a_fresh_baseline(self):
        record_sample(self.db, [connection("a", "example.com", 10, 0)], 120)
        record_sample(self.db, [connection("a", "example.com", 20, 0)], 121)
        record_sample(self.db, [connection("a", "example.com", 100, 0)], 122, force_baseline=True)
        record_sample(self.db, [connection("a", "example.com", 105, 0)], 123)
        self.assertEqual(read_stats(self.db, 123), [("example.com", 15, 0)])

    def test_clear_history_resets_totals_and_baselines_active_connections(self):
        record_sample(self.db, [connection("a", "example.com", 100, 10)], 120)
        record_sample(self.db, [connection("a", "example.com", 150, 20)], 121)
        self.assertEqual(read_stats(self.db, 121), [("example.com", 50, 10)])

        clear_history(self.db)
        self.assertEqual(read_stats(self.db, 121), [])
        record_sample(self.db, [connection("a", "example.com", 180, 25)], 122)
        self.assertEqual(read_stats(self.db, 122), [])
        record_sample(self.db, [connection("a", "example.com", 190, 30)], 123)
        self.assertEqual(read_stats(self.db, 123), [("example.com", 10, 5)])


if __name__ == "__main__":
    unittest.main()
