"""Deterministic reports (brief §6): a brief weekly signal of life over ntfy,
a monthly synthesis by email carrying the whole archive as its backup.

Every number here is computed in code. No language model touches this
module, not to compute and not to write — a narrative layer may be added
later, but it will never be allowed to produce a figure (CLAUDE.md).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from collector import archive

logger = logging.getLogger(__name__)

QUANTITIES = [
    "t_in", "rh_in", "t_out", "t_out_min", "t_out_max",
    "rh_out", "co2", "pm1", "pm25", "pm10",
]


@dataclass
class QuantityStats:
    count: int
    mean: Optional[float]
    minimum: Optional[float]
    maximum: Optional[float]


@dataclass
class MonthWindow:
    year: int
    month: int

    @property
    def start(self) -> dt.datetime:
        return dt.datetime(self.year, self.month, 1, tzinfo=dt.timezone.utc)

    @property
    def end(self) -> dt.datetime:  # exclusive
        if self.month == 12:
            return dt.datetime(self.year + 1, 1, 1, tzinfo=dt.timezone.utc)
        return dt.datetime(self.year, self.month + 1, 1, tzinfo=dt.timezone.utc)

    @property
    def total_hours(self) -> int:
        return int((self.end - self.start).total_seconds() // 3600)

    def previous(self) -> "MonthWindow":
        if self.month == 1:
            return MonthWindow(self.year - 1, 12)
        return MonthWindow(self.year, self.month - 1)

    def previous_year(self) -> "MonthWindow":
        return MonthWindow(self.year - 1, self.month)

    def label(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


# ---------------------------------------------------------------------------
# Reading the archive
# ---------------------------------------------------------------------------


def read_device_rows(archive_root: Path, device: str) -> List[Dict[str, str]]:
    """Every archived row for a device, across every schema generation file,
    in chronological order.
    """
    rows: List[Dict[str, str]] = []
    for path in archive.dated_files(archive.device_dir(archive_root, device)):
        rows.extend(archive.read_rows(path))
    return rows


def rows_in_window(rows: List[Dict[str, str]], start: dt.datetime, end: dt.datetime) -> List[Dict[str, str]]:
    start_s, end_s = start.strftime(archive.TS_FORMAT), end.strftime(archive.TS_FORMAT)
    return [r for r in rows if start_s <= r["ts_utc"] < end_s]


def events_in_window(events: List[Dict[str, str]], start: dt.datetime, end: dt.datetime) -> List[Dict[str, str]]:
    start_s, end_s = start.strftime(archive.TS_FORMAT), end.strftime(archive.TS_FORMAT)
    return [e for e in events if start_s <= e["ts_utc"] < end_s]


# ---------------------------------------------------------------------------
# Statistics — absence is excluded from the count, never treated as zero
# ---------------------------------------------------------------------------


def compute_stats(rows: List[Dict[str, str]], quantities: List[str] = QUANTITIES) -> Dict[str, QuantityStats]:
    stats = {}
    for q in quantities:
        values = [float(r[q]) for r in rows if r.get(q, "") != ""]
        if values:
            stats[q] = QuantityStats(len(values), sum(values) / len(values), min(values), max(values))
        else:
            stats[q] = QuantityStats(0, None, None, None)
    return stats


def coverage(rows: List[Dict[str, str]], window: MonthWindow) -> Tuple[int, int]:
    """(hours present, hours possible) for the window — an honest fraction,
    never smoothed over.
    """
    return len(rows), window.total_hours


# ---------------------------------------------------------------------------
# CSV rendering — the same format everywhere (COLLECTE.md §3)
# ---------------------------------------------------------------------------


def render_csv(header: List[str], rows: List[Dict[str, str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([row.get(col, "") for col in header])
    return buf.getvalue()


def export_csv(archive_root: Path, device: str, start: dt.datetime, end: dt.datetime) -> str:
    """A derived, read-only export re-projected onto the device's current
    schema: rows from an older generation simply lack the newer trailing
    columns, which come out empty. Regenerated on every call — it is not
    the archive and does not replace it.
    """
    files = archive.dated_files(archive.device_dir(archive_root, device))
    header = archive.read_header(files[-1]) if files else archive.BASE_COLUMNS
    rows = rows_in_window(read_device_rows(archive_root, device), start, end)
    return render_csv(header, rows)


def zip_archive(archive_root: Path) -> bytes:
    """The entire archive tree (every device, every generation file, plus
    each device's state) as one zip — a complete, self-contained restoration
    point, not just the month just elapsed.
    """
    buf = io.BytesIO()
    root = Path(archive_root)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(root)))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Weekly report — brief, over ntfy
# ---------------------------------------------------------------------------


def build_weekly_report(archive_root: Path, device: str, mute_sensor_hours: int, now: Optional[dt.datetime] = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(days=7)
    rows = rows_in_window(read_device_rows(archive_root, device), start, now)

    last_ts = archive.last_archived_timestamp(archive_root, device)
    total_possible = 7 * 24
    gaps = archive.detect_gaps([r["ts_utc"] for r in rows])
    streaks = archive.muted_sensors_in_archive(archive_root, device, mute_sensor_hours)

    t_out_min_values = [float(r["t_out_min"]) for r in rows if r.get("t_out_min", "") != ""]
    t_out_max_values = [float(r["t_out_max"]) for r in rows if r.get("t_out_max", "") != ""]

    lines = [
        f"{device} — semaine du {start.strftime('%Y-%m-%d')} au {now.strftime('%Y-%m-%d')}",
        f"Derniere mesure : {last_ts or 'aucune'}",
        f"Heures collectees : {len(rows)}/{total_possible}",
        f"Trous : {len(gaps)}" + (f" ({sum(g.missing_hours for g in gaps)} h manquantes)" if gaps else ""),
        "Capteurs muets : " + (", ".join(f"{s.column} depuis {s.first_ts}" for s in streaks) if streaks else "aucun"),
        "Exterieur : " + (
            f"min {min(t_out_min_values):.1f} C, max {max(t_out_max_values):.1f} C"
            if t_out_min_values and t_out_max_values else "pas de donnee"
        ),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Monthly report — email, with the two attachments
# ---------------------------------------------------------------------------


def _format_stats_table(stats: Dict[str, QuantityStats]) -> str:
    header = f"{'Grandeur':<10} {'Moyenne':>10} {'Min':>10} {'Max':>10} {'Echantillons':>14}"
    lines = [header, "-" * len(header)]
    for q in QUANTITIES:
        s = stats[q]
        if s.count == 0:
            lines.append(f"{q:<10} {'—':>10} {'—':>10} {'—':>10} {0:>14}")
        else:
            lines.append(f"{q:<10} {s.mean:>10.2f} {s.minimum:>10.2f} {s.maximum:>10.2f} {s.count:>14}")
    return "\n".join(lines)


def _format_comparison(label: str, current: Dict[str, QuantityStats], other_rows: Optional[List[Dict[str, str]]]) -> str:
    if other_rows is None or not other_rows:
        return f"Comparaison a {label} : pas de donnee pour {label}."
    other = compute_stats(other_rows)
    lines = [f"Comparaison a {label} :"]
    for q in QUANTITIES:
        cur, oth = current[q], other[q]
        if cur.mean is None or oth.mean is None:
            lines.append(f"  {q:<10} pas de donnee comparable")
            continue
        delta = cur.mean - oth.mean
        lines.append(f"  {q:<10} moyenne {cur.mean:.2f} vs {oth.mean:.2f} ({delta:+.2f})")
    return "\n".join(lines)


def _format_overwritten_bilan(month_events: List[Dict[str, str]], current_overwritten: Optional[int]) -> str:
    """The month's bilan comes from the event journal alone — never from a
    live query. The journal is durable; state.json (where the running
    `overwritten` counter otherwise lives) is deliberately reconstructible
    from the archive, and reconstruction cannot recover a loss history that
    exists nowhere else. The live value, when available, is shown only as a
    supplementary "as of now" figure, never as the source of the bilan.

    Two event types, two rubrics — never merged into one total. A
    `overwrite_detected` entry carries the archived row's own ts_utc: it is
    dated, and counts toward the month it actually happened in.
    `overwrite_suspected` is what the live counter alone can offer, stamped
    with poll time because the row that would have dated it precisely was
    itself overwritten before it could be collected. Folding the two into
    one total would present an unknown date as a known one — filing an
    undated loss under "this month" is a false certainty, not a rounding
    error.
    """
    dated = [int(e["detail"]) for e in month_events if e["event"] == "overwrite_detected"]
    suspected = [e for e in month_events if e["event"] == "overwrite_suspected"]

    if dated:
        dated_line = f"{sum(dated)} enregistrement(s) perdu(s) sur {len(dated)} evenement(s) dates ce mois-ci."
    else:
        dated_line = "aucune perte datee ce mois-ci."

    if suspected:
        suspected_lines = [
            "Pertes suspectees, date de survenue inconnue (non comptees dans le total du mois) :"
        ]
        for e in suspected:
            suspected_lines.append(
                f"  - {e['detail']} enregistrement(s), detectee le {e['ts_utc']} (overwritten={e['context']})"
            )
        suspected_block = "\n".join(suspected_lines)
    else:
        suspected_block = "Pertes suspectees, date de survenue inconnue : aucune."

    current = (
        f"Compteur actuel de l'appareil (en direct) : {current_overwritten}."
        if current_overwritten is not None
        else "Compteur actuel de l'appareil : non disponible (appareil injoignable au moment du rapport)."
    )
    return f"Ecrasements : {dated_line}\n{suspected_block}\n{current}"


def build_monthly_report(
    archive_root: Path,
    device: str,
    window: MonthWindow,
    max_attachment_mb: int,
    current_overwritten: Optional[int] = None,
) -> Tuple[str, List[Tuple[str, bytes, str]]]:
    """Returns (body, attachments). Attachments: the month's export, then
    the full archive zip — in that order, matching the brief's table.
    """
    all_rows = read_device_rows(archive_root, device)
    month_rows = rows_in_window(all_rows, window.start, window.end)
    stats = compute_stats(month_rows)
    present, possible = coverage(month_rows, window)
    coverage_pct = (100.0 * present / possible) if possible else 0.0

    prev = window.previous()
    prev_rows = rows_in_window(all_rows, prev.start, prev.end)
    prev_year = window.previous_year()
    prev_year_rows = rows_in_window(all_rows, prev_year.start, prev_year.end)

    gaps = archive.detect_gaps([r["ts_utc"] for r in month_rows])
    month_events = events_in_window(archive.read_events(archive_root, device), window.start, window.end)

    lines = [
        f"Rapport mensuel — {device} — {window.label()}",
        "",
        f"Couverture : {present}/{possible} heures ({coverage_pct:.1f}%)",
        "",
        _format_stats_table(stats),
        "",
        _format_comparison(prev.label(), stats, prev_rows if prev_rows else None),
        "",
        _format_comparison(prev_year.label(), stats, prev_year_rows if prev_year_rows else None),
        "",
        f"Trous : {len(gaps)}" + (f" ({sum(g.missing_hours for g in gaps)} h manquantes)" if gaps else ""),
        _format_overwritten_bilan(month_events, current_overwritten),
    ]
    body = "\n".join(lines)

    export = export_csv(archive_root, device, window.start, window.end).encode("ascii")
    archive_zip = zip_archive(archive_root)

    zip_mb = len(archive_zip) / 1_000_000
    if zip_mb > max_attachment_mb:
        logger.warning(
            "archive attachment is %.1f MB, over the configured %d MB threshold", zip_mb, max_attachment_mb
        )

    attachments = [
        (f"{device}-{window.label()}.csv", export, "text/csv"),
        ("archive.zip", archive_zip, "application/zip"),
    ]
    return body, attachments
