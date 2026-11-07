"""The national band table: what Finland's Regulation M4 allocates, at sub-band resolution.

`build_band_table` turns Traficom's Frequency Allocation Table rows into flagged bands; the
tools/build_m4_bands.py command writes the result to data/national/. `load_m4_conflict_bands`
reads it back for the screening rule.

How the flags follow the source (M4 Appendix 1, "Definitions", page 1 (3), PDF page 240):
- Upper case services are primary, lower case secondary.
- "In mobile and fixed services, the centre frequencies of the extreme channels are the lower
  and upper limits of a sub-band. In other radio services, the sub-band limits form the limits
  for the given usage." So a stretch of a table band with no row at all still has that band's
  primary terrestrial service (recorded in `no_row_gaps_mhz`), but never a satellite allocation.

A satellite service with no direction in its text takes it from an earth station row for the
same sub-band. When there is none the direction stays unresolved: it is logged, not guessed.
"""
import csv
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BAND_COLUMNS = [
    "band_low_mhz", "band_high_mhz", "table_band_low_mhz", "table_band_high_mhz",
    "primary_services", "secondary_services", "finnish_terrestrial_use",
    "space_down_allocated", "space_up_allocated", "space_down_unresolved",
    "passive_or_radio_astronomy", "terrestrial_primary", "concern_if_satellite_transmits",
    "no_row_gaps_mhz", "notes", "api_row_ids", "m4_table_pages", "source",
]
LOG_COLUMNS = ["category", "service_text", "low_mhz", "high_mhz", "api_row_ids", "reason"]

TERRESTRIAL = {
    "AERONAUTICAL MOBILE", "AERONAUTICAL RADIONAVIGATION", "AMATEUR", "BROADCASTING", "FIXED",
    "FIXED AND MOBILE", "LAND MOBILE", "MARITIME MOBILE", "MARITIME RADIONAVIGATION",
    "METEOROLOGICAL AIDS", "MOBILE", "MOBILE AND FIXED", "RADIOLOCATION", "RADIONAVIGATION",
    "STANDARD FREQUENCY AND TIME SIGNAL",
}
SATELLITE = {
    "AERONAUTICAL MOBILE-SATELLITE", "AMATEUR-SATELLITE", "BROADCASTING-SATELLITE",
    "EARTH EXPLORATION-SATELLITE", "FIXED SATELLITE", "INTER-SATELLITE", "METEOROLOGICAL-SATELLITE",
    "MOBILE-SATELLITE", "RADIODETERMINATION SATELLITE SERVICE", "RADIONAVIGATION-SATELLITE",
    "SPACE OPERATION", "SPACE RESEARCH", "STANDARD FREQUENCY AND TIME SIGNAL-SATELLITE",
}
# Rows with a station class exist for this service in 11.7-12.5 and 40.5-42.5 GHz, always space
# station TX with earth station RX. Its other rows (21.4-22, 74-75.5 GHz) carry no class.
ALWAYS_SPACE_TO_EARTH = {"BROADCASTING-SATELLITE"}
NEVER_SPACE_TO_EARTH = {"INTER-SATELLITE"}
PARENTHESES = {"R", "OR", "SPACE-TO-EARTH", "EARTH-TO-SPACE", "SPACE-TO-SPACE", "PASSIVE", "ACTIVE"}
NOTE_WORTHY = re.compile(r"not in use in finland|all emissions prohibited|receiving satellite earth station", re.I)


@dataclass(frozen=True)
class Service:
    kind: str  # terrestrial, satellite, passive, none or unclassified
    primary: bool
    down: bool | None = None  # None: a satellite service whose text gives no direction
    up: bool | None = None
    reason: str = ""


def classify_service(text: str, additional: str = "") -> Service:
    """Reads one Services_in_Finland value. Text it does not recognise comes back unclassified."""
    clean = " ".join(text.split())
    if clean.lower() == "(not allocated)":
        return Service("none", False, False, False)
    base, _, rest = clean.partition("(")
    primary = base.strip().isupper()
    name = base.strip().upper()
    tokens = {t.strip().upper() for t in rest.rstrip(") ").split(",") if t.strip()}
    if unknown := tokens - PARENTHESES:
        return Service("unclassified", primary, reason=f"unknown qualifier {sorted(unknown)}")
    if name == "RADIO ASTRONOMY":
        return Service("passive", primary, False, False)
    if name in TERRESTRIAL:
        return Service("terrestrial", primary, False, False)
    if name not in SATELLITE:
        return Service("unclassified", primary, reason="service name not recognised")
    if "PASSIVE" in tokens or additional.strip().lower() == "passive":
        return Service("passive", primary, False, False)
    if "ACTIVE" in tokens or name in NEVER_SPACE_TO_EARTH:
        return Service("satellite", primary, False, False)
    if tokens & {"SPACE-TO-EARTH", "EARTH-TO-SPACE", "SPACE-TO-SPACE"}:
        return Service("satellite", primary, "SPACE-TO-EARTH" in tokens, "EARTH-TO-SPACE" in tokens)
    if name in ALWAYS_SPACE_TO_EARTH:
        return Service("satellite", primary, True, False)
    return Service("satellite", primary, None, None)


def _hz(text: str) -> int:
    match = re.fullmatch(r"\s*([\d.]+)\s*(kHz|MHz|GHz)\s*", text)
    if not match:
        raise ValueError(f"Cannot read frequency {text!r}")
    return round(float(match[1]) * {"kHz": 1e3, "MHz": 1e6, "GHz": 1e9}[match[2]])


def _mhz(hz: int) -> str:
    return f"{hz / 1e6:.6f}".rstrip("0").rstrip(".")


def _id_ranges(ids: Iterable[int]) -> str:
    spans: list[list[int]] = []
    for row_id in sorted(set(ids)):
        if spans and row_id == spans[-1][1] + 1:
            spans[-1][1] = row_id
        else:
            spans.append([row_id, row_id])
    return " ".join(str(a) if a == b else f"{a}-{b}" for a, b in spans)


def _service_label(row: dict[str, Any]) -> str:
    text = " ".join(row["Services_in_Finland"].split())
    extra = " ".join(row["Additional_information"].split())
    return f"{text} ({extra})" if extra.startswith("except") else text


def _earth_station_evidence(rows: list[dict[str, Any]]) -> tuple[bool, bool]:
    """(down, up) that earth station rows prove. A space station row alone proves nothing: it
    can be an active sensor (1215-1300 MHz) or space-to-space (2070-2110 MHz)."""
    down = up = False
    for row in rows:
        if "earth station" in row["Class_of_station"].lower():
            down = down or "RX" in row["Direction"]
            up = up or "TX" in row["Direction"]
    return down, up


def _classified_rows(rows: list[dict[str, Any]]) -> tuple[dict[int, Service], list[dict[str, Any]]]:
    """Every row's service with directions resolved where the table allows it, and the log."""
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (row["Services_in_Finland"].strip(), row["Additional_information"].strip(),
               row["Sub_band_lower_limit__Hz_"], row["Sub_band_upper_limit__Hz_"])
        groups[key].append(row)
    services: dict[int, Service] = {}
    log: list[dict[str, Any]] = []
    for (text, additional, low, high), members in groups.items():
        service = classify_service(text, additional)
        ids = _id_ranges(r["ID"] for r in members)
        entry = {"service_text": text, "low_mhz": _mhz(low), "high_mhz": _mhz(high), "api_row_ids": ids}
        if service.kind == "unclassified":
            log.append({"category": "unclassified_service", "reason": service.reason, **entry})
        elif service.kind == "satellite" and service.down is None:
            down, up = _earth_station_evidence(members)
            if down or up:
                service = Service("satellite", service.primary, down, up)
            else:
                log.append({"category": "direction_unresolved", "reason": (
                    "No direction in the service text and no earth station row for this sub-band; "
                    "not counted as a space-to-Earth allocation."), **entry})
        for row in members:
            services[row["ID"]] = service
            if service.kind != "passive" and re.search(r"\bpassive\b", row["Comment"], re.I):
                log.append({"category": "comment_says_passive", "reason": (
                    f"Comment reads {row['Comment'].strip()!r}, service text does not; "
                    "passive flag not set."), **{**entry, "api_row_ids": str(row["ID"])}})
    log.sort(key=lambda e: (e["category"], float(e["low_mhz"]), float(e["high_mhz"]), e["service_text"]))
    return services, log


def _concern(down: bool, terrestrial: bool, passive: bool) -> str:
    if passive:
        return "high: passive/radio astronomy band, any emission is a concern"
    if terrestrial and not down:
        return "high: Finnish terrestrial primary use, no space-to-Earth allocation in Finland"
    if terrestrial:
        return "medium: shared band, check pfd against RR Art. 21 / App. 5"
    return "low/medium: space-only band, check Finnish earth stations and satellites" if down else "low"


def _pages_for(low: int, high: int, pages: list[tuple[float, float, str]]) -> str:
    """Printed M4 table pages for this range, from the (low, high, 'first-last') index given."""
    found = [p for lo, hi, text in pages if lo < high / 1e6 and hi > low / 1e6 for p in text.split("-")]
    numbers = sorted(int(p) for p in found if p.strip().isdigit())
    return f"{numbers[0]}-{numbers[-1]}" if numbers else ""


def build_band_table(
    rows: list[dict[str, Any]], pages: list[tuple[float, float, str]] = (), source: str = ""
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (bands, log). Stays inside one table band, and splits it only where a flag changes."""
    services, log = _classified_rows(rows)
    table_bands = sorted({(_hz(r["Frequency_band_lower_limit"]), _hz(r["Frequency_band_upper_limit"])) for r in rows})
    edges = sorted({e for band in table_bands for e in band}
                   | {r["Sub_band_lower_limit__Hz_"] for r in rows} | {r["Sub_band_upper_limit__Hz_"] for r in rows})
    covering: dict[int, list[dict[str, Any]]] = defaultdict(list)  # segment index -> rows
    for row in rows:
        for i in range(bisect_left(edges, row["Sub_band_lower_limit__Hz_"]), bisect_left(edges, row["Sub_band_upper_limit__Hz_"])):
            covering[i].append(row)
    terrestrial_in_table_band = {
        band: any(
            services[r["ID"]].kind == "terrestrial" and services[r["ID"]].primary
            for r in rows
            if (_hz(r["Frequency_band_lower_limit"]), _hz(r["Frequency_band_upper_limit"])) == band
        )
        for band in table_bands
    }

    bands: list[dict[str, Any]] = []
    open_band: dict[str, Any] | None = None
    for band in table_bands:
        first, last = bisect_right(edges, band[0]) - 1, bisect_left(edges, band[1])
        open_band = None
        for i in range(first, last):
            members = covering.get(i, [])
            kinds = [services[r["ID"]] for r in members]
            down = any(s.down for s in kinds)
            flags = (
                down,
                any(s.up for s in kinds),
                not down and any(s.down is None for s in kinds),
                any(s.kind == "passive" and s.primary for s in kinds),
                any(s.kind == "terrestrial" and s.primary for s in kinds) if members else terrestrial_in_table_band[band],
            )
            if open_band is None or open_band["flags"] != flags:
                open_band = {"flags": flags, "low": edges[i], "high": edges[i + 1], "rows": {}, "gaps": []}
                bands.append(open_band | {"table_band": band})
                open_band = bands[-1]
            open_band["high"] = edges[i + 1]
            open_band["rows"].update({r["ID"]: r for r in members})
            if not members:
                open_band["gaps"].append((edges[i], edges[i + 1]))
    return [_band_record(b, services, pages, source) for b in bands], log


def _band_record(band: dict[str, Any], services: dict[int, Service], pages, source: str) -> dict[str, Any]:
    down, up, _, passive, terrestrial = band["flags"]
    rows = sorted(band["rows"].values(), key=lambda r: r["ID"])
    labels = {_service_label(r): services[r["ID"]] for r in rows}
    return {
        "band_low_mhz": _mhz(band["low"]),
        "band_high_mhz": _mhz(band["high"]),
        "table_band_low_mhz": _mhz(band["table_band"][0]),
        "table_band_high_mhz": _mhz(band["table_band"][1]),
        "primary_services": "; ".join(sorted(k for k, s in labels.items() if s.primary)),
        "secondary_services": "; ".join(sorted(k for k, s in labels.items() if not s.primary)),
        "finnish_terrestrial_use": "; ".join(sorted({
            " ".join(r["Sub_band_usage"].split()) for r in rows if services[r["ID"]].kind == "terrestrial"
        })),
        "space_down_allocated": down,
        "space_up_allocated": up,
        "space_down_unresolved": "" if down else "; ".join(sorted(k for k, s in labels.items() if s.down is None)),
        "passive_or_radio_astronomy": passive,
        "terrestrial_primary": terrestrial,
        "concern_if_satellite_transmits": _concern(down, terrestrial, passive),
        "no_row_gaps_mhz": " ".join(f"{_mhz(a)}-{_mhz(b)}" for a, b in _joined(band["gaps"])),
        "notes": " | ".join(sorted({" ".join(r["Comment"].split()) for r in rows if NOTE_WORTHY.search(r["Comment"])})),
        "api_row_ids": _id_ranges(r["ID"] for r in rows),
        "m4_table_pages": _pages_for(band["low"], band["high"], list(pages)),
        "source": source,
    }


def _joined(gaps: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for low, high in gaps:
        if out and out[-1][1] == low:
            out[-1] = (out[-1][0], high)
        else:
            out.append((low, high))
    return out


@dataclass(frozen=True)
class M4Band:
    low_mhz: float
    high_mhz: float
    primary_services: str
    finnish_use: str
    passive: bool
    concern: str
    pages: str
    api_row_ids: str = ""
    space_down_unresolved: str = ""
    no_row_gaps_mhz: str = ""
    source: str = ""


def load_m4_conflict_bands(path: Path) -> list[M4Band]:
    """Bands with Finnish terrestrial or passive use and no space-to-Earth allocation in Finland.

    A band whose only possible downlink is a service of unresolved direction counts as a conflict:
    screening is conservative, and the case then says so (ARCHITECTURE.md principle 6).
    """
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    flag = lambda value: value.strip().lower() == "true"  # noqa: E731
    return [
        M4Band(
            float(r["band_low_mhz"]),
            float(r["band_high_mhz"]),
            r["primary_services"],
            r["finnish_terrestrial_use"],
            flag(r["passive_or_radio_astronomy"]),
            r["concern_if_satellite_transmits"],
            r["m4_table_pages"],
            r.get("api_row_ids", ""),
            r.get("space_down_unresolved", ""),
            r.get("no_row_gaps_mhz", ""),
            r.get("source", ""),
        )
        for r in rows
        if not flag(r["space_down_allocated"]) and (flag(r["terrestrial_primary"]) or flag(r["passive_or_radio_astronomy"]))
    ]
