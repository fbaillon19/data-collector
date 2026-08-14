"""CLI entry points (brief §8).

Exit code is non-zero on any failure, so the systemd timer's own journal is
the fallback record — the one that survives even if ntfy or SMTP are
themselves the thing that's down.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from collector import archive, config, fetch, notify, report, state

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime(archive.TS_FORMAT)


def _alert(cfg: config.Config, title: str, message: str, priority: str = "default") -> None:
    """Logs and swallows a notification failure: one broken channel must not
    stop the rest of the poll, but must never vanish silently either.
    """
    try:
        notify.send_ntfy(cfg.ntfy, title=title, message=message, priority=priority)
    except notify.NotificationError:
        logger.error("alert could not be delivered: %s — %s", title, message)


def _handle_unreachable(
    cfg: config.Config, device: config.DeviceConfig, device_state: Dict[str, Any], detail: str
) -> None:
    if device_state["consecutive_failures"] == 0:
        # Episode boundary, not a per-poll event: logged once, on the very
        # first failure — independent of unreachable_polls, which only
        # gates the push alert. A short outage that never reaches the
        # threshold still deserves an entry, so the monthly report can
        # later explain a gap it would otherwise leave unexplained.
        archive.log_event(cfg.archive.dir, device.name, "device_unreachable_start", detail=detail)
    device_state["consecutive_failures"] += 1
    n, threshold = device_state["consecutive_failures"], cfg.alerts.unreachable_polls
    logger.warning("%s unreachable (%d/%d): %s", device.name, n, threshold, detail)
    if n == threshold:
        _alert(
            cfg,
            f"{device.name} injoignable",
            f"Aucune reponse apres {threshold} passages : {detail}",
            priority="high",
        )
        device_state["last_alerts"]["unreachable"] = _now_iso()


def _check_overwritten(
    cfg: config.Config,
    device: config.DeviceConfig,
    device_state: Dict[str, Any],
    status: Dict[str, str],
    column_already_logged: bool,
) -> None:
    """`column_already_logged` is True when this same poll's batch already
    contained rows with `overwrote=1` — write_batch has already journaled
    those, row by row, before this function runs. The `overwrote` column is
    the authoritative source (COLLECTE.md §4); the live counter is a
    fallback, not a second witness. When both agree, only the column's
    entries stand — logging the counter's delta too would double-count the
    same loss under two entries.
    """
    try:
        current = int(status["overwritten"])
    except (KeyError, ValueError):
        logger.warning("%s: status has no valid 'overwritten' field", device.name)
        return

    change = archive.check_overwritten(device_state["last_overwritten"], current)
    if change is not None:
        if change.kind == "loss":
            if not column_already_logged:
                # Undated: the row that would have carried overwrote=1 was
                # itself overwritten before it could be collected — there is
                # no ts_utc to anchor this loss to, and none to reconstruct.
                # A distinct event name, not just a flag, so events.csv
                # itself says so to anyone reading it without report.py.
                archive.log_event(
                    cfg.archive.dir, device.name, "overwrite_suspected",
                    detail=str(change.delta), context=str(change.current),
                )
            _alert(
                cfg,
                f"{device.name} : perte definitive",
                f"{change.delta} enregistrement(s) ecrases avant collecte (overwritten={change.current}).",
                priority="high",
            )
            device_state["last_alerts"]["overwritten"] = _now_iso()
        elif change.kind == "restart":
            # Not a loss by itself — the `overwrote` column is what proves
            # that — but a fact that must not disappear: the counter can no
            # longer be trusted as a running total from here.
            archive.log_event(
                cfg.archive.dir, device.name, "device_restarted",
                detail=str(change.current), context=str(change.previous),
            )
    device_state["last_overwritten"] = current


def _check_time_untrusted(
    cfg: config.Config, device: config.DeviceConfig, device_state: Dict[str, Any], status: Dict[str, str]
) -> None:
    untrusted = archive.is_time_untrusted(status)
    if untrusted and device_state["last_alerts"]["time_untrusted"] is None:
        archive.log_event(cfg.archive.dir, device.name, "time_untrusted")
        _alert(
            cfg,
            f"{device.name} : heure non fiable",
            "time_trusted=0 : aucune nouvelle mesure ne sera datee tant que ce n'est pas resolu.",
            priority="high",
        )
        device_state["last_alerts"]["time_untrusted"] = _now_iso()
    elif not untrusted:
        device_state["last_alerts"]["time_untrusted"] = None


def _check_muted_sensors(
    cfg: config.Config, device: config.DeviceConfig, device_state: Dict[str, Any], archive_root: Path
) -> None:
    streaks = archive.muted_sensors_in_archive(archive_root, device.name, cfg.alerts.mute_sensor_hours)
    previously_alerted = device_state["last_alerts"].get("mute_sensor")
    if not isinstance(previously_alerted, dict):
        previously_alerted = {}

    current_markers = {}
    for streak in streaks:
        current_markers[streak.column] = streak.first_ts
        if previously_alerted.get(streak.column) == streak.first_ts:
            continue  # already alerted for this exact silent episode
        archive.log_event(
            archive_root, device.name, "sensor_mute_start", detail=streak.column, ts=streak.first_ts
        )
        _alert(
            cfg,
            f"{device.name} : capteur muet",
            f"{streak.column} silencieux depuis {streak.first_ts} ({streak.hours} h).",
        )

    for column in previously_alerted:
        if column not in current_markers:
            archive.log_event(archive_root, device.name, "sensor_mute_end", detail=column)

    device_state["last_alerts"]["mute_sensor"] = current_markers or None


def _poll_device(cfg: config.Config, device: config.DeviceConfig, archive_root: Path) -> bool:
    """Returns whether this device's poll succeeded end to end."""
    device_state = state.load(archive_root, device.name)

    try:
        status = fetch.fetch_status(device.url, device.timeout_s)
        batch = fetch.fetch_history(device.url, since=device_state["last_ts_utc"], timeout_s=device.timeout_s)
    except fetch.DeviceUnhealthyError as exc:
        _handle_unreachable(cfg, device, device_state, detail=f"repond mais est en erreur ({exc})")
        state.save(archive_root, device.name, device_state)
        return False
    except fetch.DeviceUnreachableError as exc:
        _handle_unreachable(cfg, device, device_state, detail=str(exc))
        state.save(archive_root, device.name, device_state)
        return False
    except fetch.MalformedBatchError as exc:
        logger.error("malformed batch from %s: %s", device.name, exc)
        _alert(cfg, f"{device.name} : donnees corrompues", str(exc), priority="high")
        state.save(archive_root, device.name, device_state)
        return False

    if device_state["consecutive_failures"] > 0:
        archive.log_event(archive_root, device.name, "device_unreachable_end")
        device_state["consecutive_failures"] = 0
        device_state["last_alerts"]["unreachable"] = None

    try:
        result = archive.write_batch(archive_root, device.name, batch.header, batch.rows)
    except archive.SchemaViolation as exc:
        logger.error("schema violation from %s: %s", device.name, exc)
        _alert(cfg, f"{device.name} : violation de schema", str(exc), priority="high")
        state.save(archive_root, device.name, device_state)
        return False

    device_state["last_ts_utc"] = archive.last_archived_timestamp(archive_root, device.name)
    for gap in result.gaps:
        logger.warning(
            "gap for %s: %s -> %s (%d h missing)", device.name, gap.after_ts, gap.before_ts, gap.missing_hours
        )

    _check_overwritten(cfg, device, device_state, status, column_already_logged=result.overwrote_count > 0)
    _check_time_untrusted(cfg, device, device_state, status)
    _check_muted_sensors(cfg, device, device_state, archive_root)

    state.save(archive_root, device.name, device_state)
    return True


def poll(cfg: config.Config) -> bool:
    ok = True
    for device in cfg.devices:
        try:
            if not _poll_device(cfg, device, cfg.archive.dir):
                ok = False
        except Exception:
            logger.exception("unexpected error polling %s", device.name)
            ok = False
    return ok


def weekly_report(cfg: config.Config) -> bool:
    ok = True
    for device in cfg.devices:
        try:
            body = report.build_weekly_report(cfg.archive.dir, device.name, cfg.alerts.mute_sensor_hours)
            notify.send_ntfy(cfg.ntfy, title=f"{device.name} : rapport hebdomadaire", message=body)
        except notify.NotificationError:
            logger.error("weekly report could not be sent for %s", device.name)
            ok = False
        except Exception:
            logger.exception("unexpected error building the weekly report for %s", device.name)
            ok = False
    return ok


def monthly_report(cfg: config.Config, window: Optional[report.MonthWindow] = None) -> bool:
    if window is None:
        today = dt.datetime.now(dt.timezone.utc)
        window = report.MonthWindow(today.year, today.month).previous()  # the month that just ended

    ok = True
    for device in cfg.devices:
        try:
            current_overwritten = None
            try:
                status = fetch.fetch_status(device.url, device.timeout_s)
                current_overwritten = int(status["overwritten"])
            except (fetch.DeviceUnreachableError, KeyError, ValueError):
                logger.warning("could not read the live overwritten count for %s", device.name)

            body, attachments = report.build_monthly_report(
                cfg.archive.dir, device.name, window, cfg.archive.max_attachment_mb, current_overwritten
            )
            notify.send_email(
                cfg.smtp,
                subject=f"{device.name} : rapport mensuel {window.label()}",
                body=body,
                attachments=attachments,
            )
        except notify.NotificationError:
            logger.error("monthly report could not be sent for %s", device.name)
            ok = False
        except Exception:
            logger.exception("unexpected error building the monthly report for %s", device.name)
            ok = False
    return ok


def _parse_year_month(value: str) -> report.MonthWindow:
    year_s, month_s = value.split("-")
    return report.MonthWindow(int(year_s), int(month_s))


def export(cfg: config.Config, from_month: str, to_month: str) -> bool:
    start_window = _parse_year_month(from_month)
    end_window = _parse_year_month(to_month)
    ok = True
    for device in cfg.devices:
        try:
            csv_text = report.export_csv(cfg.archive.dir, device.name, start_window.start, end_window.end)
            out_path = Path(f"{device.name}-{from_month}_{to_month}.csv")
            out_path.write_text(csv_text, encoding="ascii")
            print(out_path)
        except Exception:
            logger.exception("unexpected error exporting %s", device.name)
            ok = False
    return ok


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(prog="python3 -m collector")
    parser.add_argument("--config", type=Path, default=None, help="chemin du config.ini (defaut: ~/.config/data-collector/config.ini)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("poll", help="interroge, archive, alerte si besoin")

    report_parser = subparsers.add_parser("report", help="rapport hebdomadaire ou mensuel")
    report_group = report_parser.add_mutually_exclusive_group(required=True)
    report_group.add_argument("--weekly", action="store_true")
    report_group.add_argument("--monthly", action="store_true")

    export_parser = subparsers.add_parser("export", help="export CSV sur une plage de mois")
    export_parser.add_argument("--from", dest="from_month", required=True, metavar="YYYY-MM")
    export_parser.add_argument("--to", dest="to_month", required=True, metavar="YYYY-MM")

    args = parser.parse_args(argv)

    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        logger.error("configuration invalide: %s", exc)
        return 1

    if args.command == "poll":
        return 0 if poll(cfg) else 1
    if args.command == "report":
        ok = weekly_report(cfg) if args.weekly else monthly_report(cfg)
        return 0 if ok else 1
    if args.command == "export":
        return 0 if export(cfg, args.from_month, args.to_month) else 1

    parser.error(f"unknown command: {args.command}")
    return 2  # unreachable, argparse.error already exits


if __name__ == "__main__":
    raise SystemExit(main())
