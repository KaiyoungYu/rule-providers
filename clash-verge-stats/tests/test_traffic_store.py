import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from traffic_store import csv_safe_target, open_db, read_stats, record_sample, source_label


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
        with patch.dict("os.environ", {"CLASH_API_URL": "http://name:token@127.0.0.1:9097/connections"}):
            self.assertEqual(source_label(), "http://127.0.0.1:9097")


if __name__ == "__main__":
    unittest.main()
