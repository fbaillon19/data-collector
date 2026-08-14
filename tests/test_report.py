import datetime as dt
import shutil
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from collector import archive, report


def _row(ts, **overrides):
    row = {col: "" for col in archive.BASE_COLUMNS}
    row.update(ts_utc=ts, n_in="30", n_out="30", n_co2="12", n_pm="6", partial="0", overwrote="0")
    row.update(
        t_in="21.5", rh_in="48.2", t_out="18.0", t_out_min="17.0", t_out_max="19.0",
        rh_out="60.0", co2="800", pm1="3.0", pm25="5.0", pm10="7.0",
    )
    row.update(overrides)
    return row


class ComputeStatsTest(unittest.TestCase):
    def test_excludes_empty_fields_from_count_mean_min_max(self):
        rows = [
            _row("2026-01-01T00:00:00Z", t_out="10.0"),
            _row("2026-01-01T01:00:00Z", t_out="20.0"),
            _row("2026-01-01T02:00:00Z", t_out=""),  # sensor silent this hour
        ]
        stats = report.compute_stats(rows, quantities=["t_out"])
        self.assertEqual(stats["t_out"].count, 2)
        self.assertEqual(stats["t_out"].mean, 15.0)
        self.assertEqual(stats["t_out"].minimum, 10.0)
        self.assertEqual(stats["t_out"].maximum, 20.0)

    def test_all_absent_gives_none_not_zero(self):
        rows = [_row("2026-01-01T00:00:00Z", t_out="")]
        stats = report.compute_stats(rows, quantities=["t_out"])
        self.assertEqual(stats["t_out"].count, 0)
        self.assertIsNone(stats["t_out"].mean)


class MonthWindowTest(unittest.TestCase):
    def test_total_hours_february_non_leap_year(self):
        self.assertEqual(report.MonthWindow(2026, 2).total_hours, 28 * 24)

    def test_total_hours_february_leap_year(self):
        self.assertEqual(report.MonthWindow(2028, 2).total_hours, 29 * 24)

    def test_previous_wraps_year_boundary(self):
        prev = report.MonthWindow(2026, 1).previous()
        self.assertEqual((prev.year, prev.month), (2025, 12))

    def test_previous_year_same_month(self):
        prev_year = report.MonthWindow(2026, 7).previous_year()
        self.assertEqual((prev_year.year, prev_year.month), (2025, 7))


class MonthlyReportCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive_root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rows):
        archive.write_batch(self.archive_root, "clock", archive.BASE_COLUMNS, rows)


class Criterion9_PartialCoverageIsShownWithItsCoverage(MonthlyReportCase):
    def test_partial_month_states_its_own_coverage(self):
        # Only the first 100 hours of July 2026 (744 possible) are archived.
        rows = [_row(f"2026-07-{1 + h // 24:02d}T{h % 24:02d}:00:00Z") for h in range(100)]
        self._write(rows)

        window = report.MonthWindow(2026, 7)
        body, _ = report.build_monthly_report(self.archive_root, "clock", window, max_attachment_mb=20)

        self.assertIn("100/744", body)
        self.assertIn("Trous", body)


class Criterion10_ImpossibleComparisonIsWrittenNotOmitted(MonthlyReportCase):
    def test_missing_previous_year_is_stated_explicitly(self):
        rows = [_row(f"2026-07-{1 + h // 24:02d}T{h % 24:02d}:00:00Z") for h in range(48)]
        self._write(rows)

        window = report.MonthWindow(2026, 7)
        body, _ = report.build_monthly_report(self.archive_root, "clock", window, max_attachment_mb=20)

        self.assertIn("pas de donnee pour 2025-07", body)

    def test_available_previous_month_is_compared_not_skipped(self):
        june_rows = [_row(f"2026-06-{1 + h // 24:02d}T{h % 24:02d}:00:00Z", t_out="10.0") for h in range(48)]
        july_rows = [_row(f"2026-07-{1 + h // 24:02d}T{h % 24:02d}:00:00Z", t_out="20.0") for h in range(48)]
        self._write(june_rows)
        self._write(july_rows)

        window = report.MonthWindow(2026, 7)
        body, _ = report.build_monthly_report(self.archive_root, "clock", window, max_attachment_mb=20)

        self.assertIn("Comparaison a 2026-06", body)
        self.assertIn("t_out", body)
        self.assertNotIn("pas de donnee pour 2026-06", body)


class Criterion3_OverwrittenBilanComesFromTheJournalNotTheDevice(MonthlyReportCase):
    def test_bilan_is_correct_with_no_live_value_available(self):
        self._write([_row("2026-07-01T00:00:00Z")])
        archive.log_event(self.archive_root, "clock", "overwrite_detected", detail="17", ts="2026-07-03T14:00:00Z")
        archive.log_event(self.archive_root, "clock", "overwrite_detected", detail="5", ts="2026-07-20T09:00:00Z")
        # An event outside the window must not bleed into this month's bilan.
        archive.log_event(self.archive_root, "clock", "overwrite_detected", detail="99", ts="2026-06-01T00:00:00Z")

        window = report.MonthWindow(2026, 7)
        # current_overwritten=None: exactly what main.monthly_report passes
        # when the device could not be reached to ask it live.
        body, _ = report.build_monthly_report(
            self.archive_root, "clock", window, max_attachment_mb=20, current_overwritten=None
        )

        self.assertIn("22 enregistrement(s) perdu(s) sur 2 evenement(s) dates ce mois-ci", body)
        self.assertNotIn("99", body)
        self.assertIn("non disponible", body)  # the live counter line, honestly absent

    def test_no_events_gives_an_explicit_none_not_a_silent_omission(self):
        self._write([_row("2026-07-01T00:00:00Z")])
        window = report.MonthWindow(2026, 7)
        body, _ = report.build_monthly_report(self.archive_root, "clock", window, max_attachment_mb=20)
        self.assertIn("aucune perte datee ce mois-ci", body)
        self.assertIn("Pertes suspectees, date de survenue inconnue : aucune", body)


class Addendum_UndatedLossesStayOutOfTheMonthlyTotal(MonthlyReportCase):
    """design/briefs/0002-brief-colonne-overwrote.md, addendum du 2026-08-13:
    an undated (counter-only) loss must never be filed under a month it
    cannot be shown to belong to. Two rubrics, and the total counts only
    what is dated.
    """

    def test_month_with_both_origins_keeps_them_in_separate_rubrics(self):
        self._write([_row("2026-07-01T00:00:00Z")])
        archive.log_event(self.archive_root, "clock", "overwrite_detected", detail="17", ts="2026-07-03T14:00:00Z")
        archive.log_event(
            self.archive_root, "clock", "overwrite_suspected", detail="5", context="22", ts="2026-07-20T09:00:00Z"
        )

        window = report.MonthWindow(2026, 7)
        body, _ = report.build_monthly_report(self.archive_root, "clock", window, max_attachment_mb=20)

        # The total counts only the dated loss — the undated one must never
        # be silently folded in, inflating a figure the archive can't back.
        self.assertIn("17 enregistrement(s) perdu(s) sur 1 evenement(s) dates ce mois-ci", body)
        self.assertNotIn("22 enregistrement", body)  # 17+5 must never appear as a single total
        self.assertIn("Pertes suspectees, date de survenue inconnue (non comptees dans le total du mois)", body)
        self.assertIn("5 enregistrement(s), detectee le 2026-07-20T09:00:00Z (overwritten=22)", body)


class ExportCsvTest(MonthlyReportCase):
    def test_export_uses_current_schema_older_rows_get_empty_trailing_columns(self):
        base_rows = [_row("2026-01-01T00:00:00Z")]
        self._write(base_rows)

        extended_header = archive.BASE_COLUMNS + ["vbus_mv"]
        extended_row = dict(_row("2026-01-02T00:00:00Z"), vbus_mv="5000")
        archive.write_batch(self.archive_root, "clock", extended_header, [extended_row])

        window = report.MonthWindow(2026, 1)
        csv_text = report.export_csv(self.archive_root, "clock", window.start, window.end)

        lines = csv_text.strip("\n").split("\n")
        self.assertEqual(lines[0], ",".join(extended_header))
        self.assertTrue(lines[1].endswith(","))  # old row: extension column empty
        self.assertTrue(lines[2].endswith(",5000"))


class ZipArchiveTest(MonthlyReportCase):
    def test_zip_mirrors_archive_tree_including_state(self):
        self._write([_row("2026-01-01T00:00:00Z")])
        from collector import state

        state.save(self.archive_root, "clock", state.default_state())

        data = report.zip_archive(self.archive_root)
        names = set(zipfile.ZipFile(BytesIO(data)).namelist())

        self.assertIn("clock/2026-01-01.csv", names)
        self.assertIn("clock.state.json", names)


class Criterion4_EventJournalTravelsInTheBackupAndRestores(MonthlyReportCase):
    def test_events_csv_is_zipped_and_restores_with_identical_content(self):
        self._write([_row("2026-01-01T00:00:00Z")])
        archive.log_event(self.archive_root, "clock", "overwrite_detected", detail="17", ts="2026-01-02T00:00:00Z")
        archive.log_event(self.archive_root, "clock", "sensor_mute_start", detail="n_out", ts="2026-01-03T00:00:00Z")
        original_events = archive.read_events(self.archive_root, "clock")

        data = report.zip_archive(self.archive_root)
        zf = zipfile.ZipFile(BytesIO(data))
        self.assertIn("clock/events.csv", zf.namelist())

        restore_dir = Path(tempfile.mkdtemp())
        try:
            zf.extractall(restore_dir)
            restored_events = archive.read_events(restore_dir, "clock")
            self.assertEqual(restored_events, original_events)
        finally:
            shutil.rmtree(restore_dir)


class WeeklyReportTest(MonthlyReportCase):
    def test_reports_hours_collected_and_no_gaps(self):
        now = dt.datetime(2026, 7, 8, tzinfo=dt.timezone.utc)
        rows = [
            _row((now - dt.timedelta(hours=h)).strftime(archive.TS_FORMAT))
            for h in range(48, 0, -1)
        ]
        self._write(rows)

        body = report.build_weekly_report(self.archive_root, "clock", mute_sensor_hours=6, now=now)

        self.assertIn("clock", body)
        self.assertIn("Heures collectees : 48/168", body)
        self.assertIn("Trous : 0", body)
        self.assertIn("Capteurs muets : aucun", body)


if __name__ == "__main__":
    unittest.main()
