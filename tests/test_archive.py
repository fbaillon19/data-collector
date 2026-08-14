"""Acceptance criteria 1-5 of design/briefs/0001-brief-collecteur.md, plus unit
coverage of the pure detection helpers (gaps, overwritten, muted sensors).
"""

import hashlib
import tempfile
import unittest
from pathlib import Path

from collector import archive
from collector.fetch import fetch_history
from simulator.server import serve_in_thread


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ScenarioArchiveCase(unittest.TestCase):
    scenario = "nominal"

    def setUp(self):
        self.httpd, self.thread = serve_in_thread(self.scenario)
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.tmp = tempfile.TemporaryDirectory()
        self.archive_root = Path(self.tmp.name)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def poll(self, since=None):
        batch = fetch_history(self.base_url, since=since, timeout_s=2)
        return archive.write_batch(self.archive_root, "clock", batch.header, batch.rows)


class Criterion1_NoDuplicatesAcrossPolls(ScenarioArchiveCase):
    scenario = "nominal"

    def test_two_consecutive_polls_produce_no_duplicate(self):
        first = self.poll()
        self.assertEqual(first.written, 72)
        self.assertEqual(first.duplicates, 0)

        path = archive.latest_file(archive.device_dir(self.archive_root, "clock"))
        before = _sha256(path)

        # Second poll asks for everything again (since=None), as a poll
        # would after losing its state: the archive must reject every row
        # as a duplicate rather than writing it twice.
        second = self.poll(since=None)
        self.assertEqual(second.written, 0)
        self.assertEqual(second.duplicates, 72)
        self.assertEqual(_sha256(path), before)

        with path.open(encoding="ascii") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1 + 72)  # header + 72 data rows, no more


class Criterion2_DeadSensorIsEmptyFieldZeroCount(ScenarioArchiveCase):
    scenario = "dead_sensor"

    def test_silent_hour_is_empty_field_zero_count_not_missing_row(self):
        result = self.poll()
        self.assertEqual(result.written, 24)

        path = archive.latest_file(archive.device_dir(self.archive_root, "clock"))
        with path.open(encoding="ascii") as f:
            rows = list(__import__("csv").DictReader(f))

        self.assertEqual(len(rows), 24)  # every hour is present as a row
        dead = rows[8:18]
        self.assertTrue(all(r["t_out"] == "" for r in dead))
        self.assertTrue(all(r["n_out"] == "0" for r in dead))
        self.assertTrue(all(r["n_in"] == "30" for r in dead))  # other sensor unaffected


class Criterion3_GapStaysAGap(ScenarioArchiveCase):
    scenario = "gap"

    def test_missing_hours_are_absent_rows_not_fabricated(self):
        result = self.poll()
        self.assertEqual(result.written, 69)  # 72 hours minus the 3 missing

        self.assertEqual(len(result.gaps), 1)
        gap = result.gaps[0]
        self.assertEqual(gap.missing_hours, 3)
        self.assertEqual(gap.after_ts, "2026-01-02T05:00:00Z")
        self.assertEqual(gap.before_ts, "2026-01-02T09:00:00Z")

        path = archive.latest_file(archive.device_dir(self.archive_root, "clock"))
        with path.open(encoding="ascii") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1 + 69)  # no blank/fabricated line for the gap


class Criterion4_SchemaExtensionOpensNewFile(unittest.TestCase):
    """Uses archive.write_batch directly with synthetic rows: what matters here
    is archive.py's own file-generation logic, not the HTTP round trip (already
    covered by the fetch tests and by criterion 5's simulator-backed cases).
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive_root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _row(ts, **overrides):
        row = {col: "" for col in archive.BASE_COLUMNS}
        row.update(ts_utc=ts, n_in="30", n_out="30", n_co2="12", n_pm="6", partial="0", overwrote="0")
        row.update(overrides)
        return row

    def test_extension_opens_new_file_old_file_untouched(self):
        first_rows = [self._row(f"2026-01-01T{h:02d}:00:00Z") for h in range(24)]
        archive.write_batch(self.archive_root, "clock", archive.BASE_COLUMNS, first_rows)

        old_path = archive.latest_file(archive.device_dir(self.archive_root, "clock"))
        old_hash_before = _sha256(old_path)

        extended_header = archive.BASE_COLUMNS + ["vbus_mv"]
        second_rows = [
            self._row(f"2026-01-02T{h:02d}:00:00Z", vbus_mv="5020") for h in range(5)
        ]
        result = archive.write_batch(self.archive_root, "clock", extended_header, second_rows)

        self.assertIsNotNone(result.new_file)
        self.assertNotEqual(result.new_file, old_path)
        self.assertEqual(_sha256(old_path), old_hash_before)  # old file byte-for-byte unchanged

        new_header = archive.read_header(result.new_file)
        self.assertEqual(new_header, extended_header)
        old_header = archive.read_header(old_path)
        self.assertEqual(old_header, archive.BASE_COLUMNS)


class Criterion5_SchemaViolationRejectsWholeBatch(ScenarioArchiveCase):
    """Also brief 0002 criterion 2: a header missing `overwrote` — the base
    schema was corrected before any real archive existed, so this is a
    contract violation, not a legitimate older generation.
    """

    scenario = "schema_violation"

    def test_header_missing_overwrote_is_rejected_without_partial_write(self):
        batch = fetch_history(self.base_url, since=None, timeout_s=2)
        self.assertNotIn("overwrote", batch.header)
        with self.assertRaises(archive.SchemaViolation):
            archive.write_batch(self.archive_root, "clock", batch.header, batch.rows)

        device_directory = archive.device_dir(self.archive_root, "clock")
        self.assertFalse(device_directory.exists())  # nothing written at all


class Criterion5_MalformedRowRejectsWholeBatch(ScenarioArchiveCase):
    scenario = "malformed"

    def test_malformed_row_rejected_before_archive_is_touched(self):
        from collector.fetch import MalformedBatchError

        with self.assertRaises(MalformedBatchError):
            fetch_history(self.base_url, since=None, timeout_s=2)

        device_directory = archive.device_dir(self.archive_root, "clock")
        self.assertFalse(device_directory.exists())


class OverwrittenDetectionTest(unittest.TestCase):
    def test_increase_is_classified_as_a_loss(self):
        change = archive.check_overwritten(12, 17)
        self.assertEqual(change.kind, "loss")
        self.assertEqual(change.delta, 5)

    def test_decrease_is_classified_as_a_restart(self):
        change = archive.check_overwritten(45, 3)
        self.assertEqual(change.kind, "restart")
        self.assertEqual(change.previous, 45)
        self.assertEqual(change.current, 3)
        self.assertEqual(change.delta, 42)

    def test_unchanged_is_neither(self):
        self.assertIsNone(archive.check_overwritten(12, 12))

    def test_no_previous_value_is_neither(self):
        self.assertIsNone(archive.check_overwritten(None, 12))


class Brief0002_Criterion1_OverwroteRowLogsADurableEvent(ScenarioArchiveCase):
    """brief 0002 criterion 1: a row carrying overwrote=1 must produce a
    durable overwrite_detected entry, independent of the live counter.
    """

    scenario = "overwritten"

    def test_flagged_row_produces_a_durable_overwrite_detected(self):
        result = self.poll()
        self.assertEqual(result.overwrote_count, 1)

        events = archive.read_events(self.archive_root, "clock")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "overwrite_detected")
        self.assertEqual(events[0]["detail"], "1")


class TimeUntrustedTest(unittest.TestCase):
    def test_flag_zero_means_untrusted(self):
        self.assertTrue(archive.is_time_untrusted({"time_trusted": "0"}))

    def test_flag_one_means_trusted(self):
        self.assertFalse(archive.is_time_untrusted({"time_trusted": "1"}))


class MutedSensorDetectionTest(unittest.TestCase):
    def _row(self, hour, n_out):
        return {
            "ts_utc": f"2026-01-01T{hour:02d}:00:00Z",
            "n_in": "30", "n_out": str(n_out), "n_co2": "12", "n_pm": "6",
        }

    def test_long_silent_streak_is_reported(self):
        rows = [self._row(h, 0 if 2 <= h <= 11 else 30) for h in range(24)]
        streaks = archive.detect_muted_sensors(rows, min_hours=6)
        self.assertEqual(len(streaks), 1)
        streak = streaks[0]
        self.assertEqual(streak.column, "n_out")
        self.assertEqual(streak.hours, 10)

    def test_short_silence_under_threshold_is_not_reported(self):
        rows = [self._row(h, 0 if 2 <= h <= 5 else 30) for h in range(24)]  # 4 hours
        streaks = archive.detect_muted_sensors(rows, min_hours=6)
        self.assertEqual(streaks, [])


class MutedSensorArchiveDetectionTest(unittest.TestCase):
    """A real poll writes one row at a time. detect_muted_sensors alone would
    never see a streak longer than one hour; muted_sensors_in_archive is the
    caller that reads the archive back so the streak can actually accumulate.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive_root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _row(hour):
        row = {col: "" for col in archive.BASE_COLUMNS}
        row.update(
            ts_utc=f"2026-01-01T{hour:02d}:00:00Z",
            n_in="30", n_out="0", n_co2="12", n_pm="6", partial="0", overwrote="0",
        )
        return row

    def test_alert_fires_only_once_the_archive_itself_holds_enough_silent_hours(self):
        # min_hours=6 reads as a threshold: five silent hours must not yet
        # fire, the sixth must (COLLECTE.md's "plus de X heures" is honored
        # by requiring min_hours to actually be reached, not exceeded twice).
        for hour in range(5):
            archive.write_batch(self.archive_root, "clock", archive.BASE_COLUMNS, [self._row(hour)])
            streaks = archive.muted_sensors_in_archive(self.archive_root, "clock", min_hours=6)
            self.assertEqual(streaks, [], f"should not fire yet after hour {hour}")

        archive.write_batch(self.archive_root, "clock", archive.BASE_COLUMNS, [self._row(5)])
        streaks = archive.muted_sensors_in_archive(self.archive_root, "clock", min_hours=6)

        self.assertEqual(len(streaks), 1)
        self.assertEqual(streaks[0].column, "n_out")
        self.assertEqual(streaks[0].hours, 6)

    def test_streak_start_stays_stable_once_it_outgrows_any_fixed_window(self):
        # A streak far longer than min_hours must still report — and dedup
        # against — its true first hour, not a start that drifts forward as
        # more rows accumulate.
        for hour in range(20):
            archive.write_batch(self.archive_root, "clock", archive.BASE_COLUMNS, [self._row(hour)])

        streaks = archive.muted_sensors_in_archive(self.archive_root, "clock", min_hours=6)
        self.assertEqual(len(streaks), 1)
        self.assertEqual(streaks[0].first_ts, "2026-01-01T00:00:00Z")
        self.assertEqual(streaks[0].hours, 20)


class Criterion1_UndatedFileDoesNotConfuseCurrentFileDetection(unittest.TestCase):
    """events.csv lives in the same directory as the archive's generation
    files. Before it existed, a loose "*.csv" glob would have mistaken it
    for the current file the moment its name sorted after the real one.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive_root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_events_csv_is_excluded_from_generation_file_detection(self):
        row = {col: "" for col in archive.BASE_COLUMNS}
        row.update(ts_utc="2026-01-01T00:00:00Z", n_in="30", n_out="30", n_co2="12", n_pm="6", partial="0", overwrote="0")
        archive.write_batch(self.archive_root, "clock", archive.BASE_COLUMNS, [row])

        # events.csv sorts after "2026-01-01.csv" lexically ('e' > '2'):
        # exactly the case that would fool a bare glob.
        archive.log_event(self.archive_root, "clock", "schema_extension", detail="vbus_mv")
        device_directory = archive.device_dir(self.archive_root, "clock")
        self.assertTrue((device_directory / "events.csv").exists())

        second_row = dict(row, ts_utc="2026-01-01T01:00:00Z")
        result = archive.write_batch(self.archive_root, "clock", archive.BASE_COLUMNS, [second_row])

        self.assertIsNone(result.new_file)  # appended to the real archive file, not events.csv
        latest = archive.latest_file(device_directory)
        self.assertEqual(latest.name, "2026-01-01.csv")
        with latest.open() as f:
            self.assertEqual(len(f.readlines()), 1 + 2)  # header + 2 rows, events.csv untouched by this count

    def test_dated_files_never_returns_events_csv(self):
        archive.log_event(self.archive_root, "clock", "time_untrusted")
        files = archive.dated_files(archive.device_dir(self.archive_root, "clock"))
        self.assertEqual(files, [])


class EventLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive_root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_log_event_appends_with_header_on_first_write(self):
        archive.log_event(
            self.archive_root, "clock", "overwrite_detected", detail="17", context="22", ts="2026-09-03T14:00:00Z"
        )
        events = archive.read_events(self.archive_root, "clock")
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0],
            {"ts_utc": "2026-09-03T14:00:00Z", "event": "overwrite_detected", "detail": "17", "context": "22"},
        )

    def test_context_defaults_to_empty_for_events_that_have_none(self):
        archive.log_event(self.archive_root, "clock", "time_untrusted", ts="2026-09-03T14:00:00Z")
        events = archive.read_events(self.archive_root, "clock")
        self.assertEqual(events[0]["context"], "")

    def test_events_accumulate_append_only(self):
        archive.log_event(self.archive_root, "clock", "sensor_mute_start", detail="n_out", ts="2026-09-14T02:00:00Z")
        archive.log_event(self.archive_root, "clock", "sensor_mute_end", detail="n_out", ts="2026-09-16T11:00:00Z")
        events = archive.read_events(self.archive_root, "clock")
        self.assertEqual([e["event"] for e in events], ["sensor_mute_start", "sensor_mute_end"])

    def test_no_events_file_reads_as_empty(self):
        self.assertEqual(archive.read_events(self.archive_root, "clock"), [])


class SchemaExtensionEventTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive_root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _row(ts, **overrides):
        row = {col: "" for col in archive.BASE_COLUMNS}
        row.update(ts_utc=ts, n_in="30", n_out="30", n_co2="12", n_pm="6", partial="0", overwrote="0")
        row.update(overrides)
        return row

    def test_extension_logs_an_event_first_file_does_not(self):
        archive.write_batch(self.archive_root, "clock", archive.BASE_COLUMNS, [self._row("2026-01-01T00:00:00Z")])
        events = archive.read_events(self.archive_root, "clock")
        self.assertEqual(events, [])  # the archive's first-ever file is not an "extension"

        extended = archive.BASE_COLUMNS + ["vbus_mv"]
        archive.write_batch(
            self.archive_root, "clock", extended,
            [self._row("2026-01-02T00:00:00Z", vbus_mv="5000")],
        )
        events = archive.read_events(self.archive_root, "clock")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "schema_extension")
        self.assertEqual(events[0]["detail"], "vbus_mv")


if __name__ == "__main__":
    unittest.main()
