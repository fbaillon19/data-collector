import tempfile
import unittest
from pathlib import Path

from collector import archive, state


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_state_rebuilds_from_default_when_archive_empty(self):
        loaded = state.load(self.archive_dir, "clock")
        self.assertEqual(loaded, state.default_state())

    def test_save_then_load_roundtrips(self):
        original = state.default_state()
        original["consecutive_failures"] = 2
        original["last_alerts"]["unreachable"] = "2026-08-01T00:00:00Z"
        state.save(self.archive_dir, "clock", original)

        loaded = state.load(self.archive_dir, "clock")
        self.assertEqual(loaded, original)

    def test_rebuild_recovers_last_timestamp_from_archive(self):
        header = archive.BASE_COLUMNS
        row = {col: "" for col in header}
        row["ts_utc"] = "2026-01-01T05:00:00Z"
        for c in ("n_in", "n_out", "n_co2", "n_pm", "partial"):
            row[c] = "0"
        archive.write_batch(self.archive_dir, "clock", header, [row])

        rebuilt = state.rebuild(self.archive_dir, "clock")
        self.assertEqual(rebuilt["last_ts_utc"], "2026-01-01T05:00:00Z")
        self.assertEqual(rebuilt["consecutive_failures"], 0)

    def test_corrupt_state_file_falls_back_to_rebuild(self):
        path = state.state_path(self.archive_dir, "clock")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json", encoding="ascii")

        loaded = state.load(self.archive_dir, "clock")
        self.assertIsNone(loaded["last_ts_utc"])


if __name__ == "__main__":
    unittest.main()
