"""HTTP collection against COLLECTE.md §4: paginated GET /collect/history and
GET /collect/status.

Row-level well-formedness is enforced here — a malformed row rejects the
whole batch before archive.py ever sees it (brief §3). Schema-level
validation (column names, order, whether an extension is legitimate) is
archive.py's job: it depends on what was archived before, which this module
does not know about.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional

from collector.archive import NUMERIC_COLUMNS, REQUIRED_COLUMNS, TS_FORMAT

logger = logging.getLogger(__name__)

# The device fixes its own default and cap (COLLECTE.md §4); this only
# bounds how many round trips the collector will make before giving up.
MAX_PAGES = 1000


class DeviceUnreachableError(Exception):
    """The device refused the connection or did not answer within the timeout."""


class DeviceUnhealthyError(DeviceUnreachableError):
    """The device answered, but with an HTTP error status.

    A subclass of DeviceUnreachableError, not a sibling: existing callers
    that only care "did the poll succeed" keep working unchanged. Callers
    that need the sharper diagnosis — off vs. answering but unwell, the
    more worrying case — can catch this specifically.
    """

    def __init__(self, url: str, status_code: int):
        super().__init__(f"{url}: HTTP {status_code}")
        self.status_code = status_code


class MalformedBatchError(Exception):
    """A row could not be parsed. Per contract, the whole batch is rejected."""


@dataclass
class FetchBatch:
    header: List[str]
    rows: List[Dict[str, str]]


def _get(url: str, timeout_s: float) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise DeviceUnhealthyError(url, exc.code) from exc
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        raise DeviceUnreachableError(f"{url}: {exc}") from exc


def _parse_csv(text: str) -> "tuple[List[str], List[List[str]]]":
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise MalformedBatchError("empty response: missing header row") from None
    return header, list(reader)


def _validate_row(header: List[str], fields: List[str]) -> Dict[str, str]:
    if len(fields) != len(header):
        raise MalformedBatchError(
            f"row has {len(fields)} fields, header has {len(header)}: {fields!r}"
        )
    row = dict(zip(header, fields))
    for column in REQUIRED_COLUMNS:
        if column in row and row[column] == "":
            raise MalformedBatchError(f"required column {column!r} is empty: {row!r}")
    try:
        dt.datetime.strptime(row["ts_utc"], TS_FORMAT)
    except ValueError:
        raise MalformedBatchError(f"ts_utc is not a valid UTC timestamp: {row['ts_utc']!r}") from None
    for column in NUMERIC_COLUMNS:
        value = row.get(column, "")
        if value == "":
            continue
        try:
            float(value)
        except ValueError:
            raise MalformedBatchError(f"column {column!r} is not numeric: {value!r}") from None
    return row


def fetch_history(base_url: str, since: Optional[str], timeout_s: float) -> FetchBatch:
    header: Optional[List[str]] = None
    rows: List[Dict[str, str]] = []
    cursor = since

    for _ in range(MAX_PAGES):
        url = base_url.rstrip("/") + "/collect/history"
        if cursor is not None:
            url += "?" + urllib.parse.urlencode({"since": cursor})

        text = _get(url, timeout_s).decode("ascii")
        page_header, raw_rows = _parse_csv(text)

        if header is None:
            header = page_header
        elif page_header != header:
            raise MalformedBatchError(
                f"header changed mid-pagination: {header!r} -> {page_header!r}"
            )

        if not raw_rows:
            return FetchBatch(header=header, rows=rows)

        for fields in raw_rows:
            rows.append(_validate_row(header, fields))
        cursor = rows[-1]["ts_utc"]

    logger.warning("history pagination for %s did not terminate within %d pages", base_url, MAX_PAGES)
    raise MalformedBatchError(f"pagination did not terminate within {MAX_PAGES} pages")


def fetch_status(base_url: str, timeout_s: float) -> Dict[str, str]:
    url = base_url.rstrip("/") + "/collect/status"
    text = _get(url, timeout_s).decode("ascii")
    status: Dict[str, str] = {}
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        if len(row) != 2:
            raise MalformedBatchError(f"status row is not key,value: {row!r}")
        key, value = row
        status[key] = value
    return status
