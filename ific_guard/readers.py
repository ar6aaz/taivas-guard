"""Input decoding. No parser is opened until the content cache misses."""

import base64
import datetime
import decimal
import json
import logging
import math
import re
import time
from pathlib import Path


TABLES = {
    "space": ("notice", "com_el", "grp", "provn", "geo", "non_geo", "orbit", "s_beam", "emiss"),
    "terrestrial": ("fxm_terra", "fxm_geo", "fxm_ant"),
}
REQUIRED = {"space": {"notice", "grp", "provn"}, "terrestrial": {"fxm_terra", "fxm_geo"}}
LOG = logging.getLogger(__name__)


def json_default(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return {"iso8601": value.isoformat()}
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, decimal.Decimal):
        return str(value)
    raise TypeError(f"Unsupported source value type: {type(value).__name__}")


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, default=json_default)


def read_mdb(path, domain):
    try:
        from access_parser import AccessParser
    except ImportError as exc:
        raise RuntimeError("MDB support requires: pip install '.[mdb]'") from exc
    start = time.perf_counter()
    try:
        parser = AccessParser(str(path))
    except Exception as exc:
        raise ValueError(f"Cannot decode MDB file: {path}") from exc
    LOG.info("Opened MDB catalog in %.3fs", time.perf_counter() - start)
    catalog = {name.casefold(): name for name in parser.catalog}
    missing = REQUIRED[domain] - catalog.keys()
    if missing:
        raise ValueError(f"Missing required MDB tables: {', '.join(sorted(missing))}")
    for name in TABLES[domain]:
        if name not in catalog:
            continue
        start = time.perf_counter()
        try:
            columns = parser.parse_table(catalog[name])
        except Exception as exc:
            raise ValueError(f"Cannot decode MDB table: {name}") from exc
        LOG.info("Decoded MDB table %s in %.3fs", name, time.perf_counter() - start)
        if columns is None:
            raise ValueError(f"Could not decode MDB table {name}")
        lengths = {len(values) for values in columns.values()}
        if len(lengths) > 1:
            raise ValueError(f"Inconsistent column lengths in MDB table {name}")
        # AccessParser materializes one whole table. Conversion/storage then
        # streams rows; decoding a first-time large MDB is still a cold cost.
        yield name, (
            dict(zip(columns, values))
            for values in zip(*columns.values())
        )


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).strip().replace(",", "."))
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def quantity_mhz(value, *, default_unit="MHz"):
    """Read explicit frequency units; numeric values use the documented default."""
    if value is None:
        return None
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?)\s*(Hz|kHz|MHz|GHz|k|M|G)?\s*", str(value))
    if not match:
        return None
    scale = {"Hz": 1e-6, "kHz": 1e-3, "MHz": 1, "GHz": 1000, "k": 1e-3, "M": 1, "G": 1000}
    result = number(match[1])
    return None if result is None else result * scale[match[2] or default_unit]


def station_rows(path, *, sheet_name="Frequencies of interest"):
    """Yield (source row, raw row, normalized row) without loading the workbook."""
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        import openpyxl
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = book[sheet_name]
            header = next(sheet.iter_rows(min_row=11, max_row=11, values_only=True))
            required = {"Station number", "RX frequency", "Channel bandwidth"}
            if not required.issubset(set(header)):
                raise ValueError("Expected challenge workbook English headers on row 11")
            for row_no, cells in enumerate(sheet.iter_rows(min_row=13, values_only=True), 13):
                if not any(value is not None and value != "" for value in cells):
                    continue
                raw = {str(name): value for name, value in zip(header, cells) if name is not None}
                center = quantity_mhz(raw.get("RX frequency"))
                width = quantity_mhz(raw.get("Channel bandwidth"), default_unit="kHz")
                low = center - width / 2 if center is not None and width is not None else None
                high = center + width / 2 if center is not None and width is not None else None
                tx_only = str(raw.get("Class of licence", "")).upper() in {"BC", "BT"} and raw.get("RX frequency") in (None, "")
                normalized = _station(raw.get("Station number"), low, high,
                                      raw.get("Latitude"), raw.get("Longitude"), tx_only)
                yield row_no, raw, normalized
        finally:
            book.close()
    elif path.suffix.lower() == ".csv":
        import csv
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not {"station_id", "f_low_mhz", "f_high_mhz"}.issubset(reader.fieldnames or []):
                raise ValueError("Station CSV requires station_id, f_low_mhz, f_high_mhz")
            for row_no, raw in enumerate(reader, 2):
                yield row_no, raw, _station(raw["station_id"], raw["f_low_mhz"], raw["f_high_mhz"], raw.get("latitude_deg"), raw.get("longitude_deg"))
    else:
        raise ValueError("National station input must be a challenge .xlsx or normalized .csv")


def _station(station_id, low, high, latitude, longitude, tx_only=False):
    low, high = number(low), number(high)
    usable = low is not None and high is not None and 0 <= low < high
    # Unknown reference frequencies must never masquerade as healthy zero matches.
    reason = "tx_only" if tx_only else "" if usable else "unknown_receiver_frequency"
    return (None if station_id in (None, "") else str(station_id), low, high,
            number(latitude), number(longitude), usable and not tx_only, reason)
