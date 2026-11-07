"""Reads the parts of a Space IFIC database that screening needs."""
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from functools import cached_property
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)
logging.getLogger("access_parser").setLevel(logging.CRITICAL)

Row = dict[str, Any]

# Sections the brief puts in scope. AP30/30A/30B filings are added here once an issue with them is tested.
IN_SCOPE_REASONS = {"A", "C"}  # A = advance publication (9.1/IA), C = coordination request (9.6)
SATELLITE_NOTICE_TYPES = {"G", "N"}  # G = GSO, N = non-GSO, S = earth station
SUPPORTED_DATABASE_VERSIONS = frozenset({"10"})


class DatabaseVersionError(ValueError):
    """The database schema is missing or is not supported by this reader."""


class TableReader(Protocol):
    def table(self, name: str) -> list[Row]: ...


def _tolerate_invalid_dates() -> None:
    """Makes the parser read an impossible date as null and log it.

    It otherwise raises on a date cell holding NaN or an out-of-range value, which loses the whole
    issue: Space 3077 has 22 such cells in `grp`.
    """
    from access_parser import utils

    parse = utils.mdb_date_to_readable
    if getattr(parse, "tolerant", False):
        return

    def tolerant(value):
        try:
            return parse(value)
        except (ValueError, OverflowError):
            log.warning("invalid date %r read as null", value)
            return None

    tolerant.tolerant = True
    utils.mdb_date_to_readable = tolerant


class MdbReader:
    """Reads Access tables with a pure-Python parser, so no system packages are needed."""

    def __init__(self, path: str | Path):
        from access_parser import AccessParser

        _tolerate_invalid_dates()
        self._db = AccessParser(str(path))

    def table(self, name: str) -> list[Row]:
        columns = self._db.parse_table(name)
        names = list(columns)
        return [dict(zip(names, values)) for values in zip(*columns.values())] if names else []


def read_database_version(reader: TableReader) -> str:
    """Read the SNS schema version before interpreting version-sensitive tables."""
    try:
        rows = reader.table("srs_ooak")
    except Exception as exc:
        raise DatabaseVersionError("Unable to read database version from srs_ooak.version_no") from exc
    if not rows or rows[0].get("version_no") in (None, ""):
        raise DatabaseVersionError("Database version is unavailable: srs_ooak.version_no is empty")
    raw = str(rows[0]["version_no"]).strip()
    match = re.search(r"\d+", raw)
    version = match.group(0) if match else raw
    if version not in SUPPORTED_DATABASE_VERSIONS:
        raise DatabaseVersionError(
            f"Unsupported space database version {raw!r} (srs_ooak.version_no); "
            f"supported versions: {', '.join(sorted(SUPPORTED_DATABASE_VERSIONS))}"
        )
    return version


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_date(value: Any) -> date | None:
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def add_months(start: date, months: int) -> date:
    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    days = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(year, month, min(start.day, days[month - 1]))


@dataclass
class SpaceNotice:
    ntc_id: int
    family_id: int  # a modification and the notice it modifies share one family
    adm: str | None
    ntc_type: str | None
    reason: str | None
    provision: str | None
    published: date | None
    name: str | None = None
    ref_body: str | None = None  # the body a non-GSO orbit is referred to; T is the Earth
    gso_lon_deg: float | None = None
    inclinations_deg: list[float] = field(default_factory=list)
    assignments: list[Row] = field(default_factory=list)  # rows from `freq`


class SpaceIssue:
    """Notices in screening scope, with what the rules need attached. Suppressions carry no data."""

    def __init__(self, reader: TableReader):
        self._reader = reader
        self.database_version = read_database_version(reader)

    @cached_property
    def notices(self) -> dict[int, SpaceNotice]:
        out: dict[int, SpaceNotice] = {}
        for row in self._reader.table("notice"):
            if _text(row.get("ntf_rsn")) not in IN_SCOPE_REASONS or _text(row.get("act_code")) == "S":
                continue
            ntc_id = int(row["ntc_id"])
            target = row.get("tgt_ntc_id")
            out[ntc_id] = SpaceNotice(
                ntc_id=ntc_id,
                family_id=int(target) if target not in (None, "") else ntc_id,
                adm=_text(row.get("adm")),
                ntc_type=_text(row.get("ntc_type")),
                reason=_text(row.get("ntf_rsn")),
                provision=_text(row.get("prov")),
                published=_as_date(row.get("d_wic")),
            )
        for table in ("geo", "non_geo"):
            for row in self._reader.table(table):
                notice = out.get(int(row["ntc_id"]))
                if notice:
                    notice.name = _text(row.get("sat_name"))
                    if table == "non_geo":
                        notice.ref_body = _text(row.get("ref_body"))
                    if table == "geo" and row.get("long_nom") is not None:
                        notice.gso_lon_deg = round(float(row["long_nom"]), 3)
        for ntc_id, rows in self.orbits.items():
            if notice := out.get(ntc_id):
                notice.inclinations_deg.extend(float(r["inclin_ang"]) for r in rows if r.get("inclin_ang") is not None)
        for row in self._reader.table("freq"):
            notice = out.get(int(row["ntc_id"]))
            if notice:
                notice.assignments.append(row)
        return out

    @cached_property
    def group_notice(self) -> dict[int, int]:
        return {group_id: int(row["ntc_id"]) for group_id, row in self.groups.items()}

    @cached_property
    def groups(self) -> dict[int, Row]:
        """Group rows keyed once, avoiding repeated full-table joins in rules."""
        return {int(row["grp_id"]): row for row in self._reader.table("grp")}

    @cached_property
    def emissions(self) -> dict[int, list[Row]]:
        """Notified emission rows keyed by group, including rows with no power value."""
        out: dict[int, list[Row]] = defaultdict(list)
        for row in self._reader.table("emiss"):
            out[int(row["grp_id"])].append(row)
        return dict(out)

    @cached_property
    def orbits(self) -> dict[int, list[Row]]:
        out: dict[int, list[Row]] = defaultdict(list)
        for row in self._reader.table("orbit"):
            out[int(row["ntc_id"])].append(row)
        return dict(out)

    @cached_property
    def orbit_links(self) -> dict[tuple[int, str | None, str | None], set[int]]:
        """Orbit rows each beam flies on, keyed like `beams`. No entry means the filing does not say."""
        out: dict[tuple[int, str | None, str | None], set[int]] = defaultdict(set)
        for row in self._reader.table("orbit_lnk"):
            key = (int(row["ntc_id"]), _text(row.get("emi_rcp")), _text(row.get("beam_name")))
            out[key].add(int(row["orb_id"]))
        return dict(out)

    @cached_property
    def first_phases(self) -> dict[tuple[int, int], float]:
        """Phase angle of the lowest-numbered satellite with one, per orbit row."""
        first: dict[tuple[int, int], tuple[int, float]] = {}
        for row in self._reader.table("phase"):
            if row.get("phase_ang") is None:
                continue
            key, satellite = (int(row["ntc_id"]), int(row["orb_id"])), int(row["orb_sat_id"])
            if key not in first or satellite < first[key][0]:
                first[key] = (satellite, float(row["phase_ang"]))
        return {key: value[1] for key, value in first.items()}

    @cached_property
    def beams(self) -> dict[tuple[int, str | None, str | None], list[Row]]:
        """Beam rows keyed by the join specified by the Space Preface."""
        out: dict[tuple[int, str | None, str | None], list[Row]] = defaultdict(list)
        for row in self._reader.table("s_beam"):
            key = (int(row["ntc_id"]), _text(row.get("emi_rcp")), _text(row.get("beam_name")))
            out[key].append(row)
        return dict(out)

    def provisions_for_adm(self, adm: str) -> dict[int, dict[str, set]]:
        """Where the BR names an administration, per notice: provisions and affected groups."""
        found: dict[int, dict[str, set]] = defaultdict(lambda: {"provisions": set(), "groups": set()})
        for row in self._reader.table("provn"):
            if _text(row.get("adm")) == adm or _text(row.get("ctry")) == adm:
                ntc_id = self.group_notice.get(int(row["grp_id"]))
                if ntc_id in self.notices:
                    found[ntc_id]["groups"].add(int(row["grp_id"]))
                    if prov := _text(row.get("coord_prov")):
                        found[ntc_id]["provisions"].add(prov)
        for row in self._reader.table("tr_provn"):
            if _text(row.get("adm")) == adm or _text(row.get("ctry")) == adm:
                ntc_id = int(row["ntc_id"])
                if ntc_id in self.notices and (prov := _text(row.get("coord_prov"))):
                    found[ntc_id]["provisions"].add(prov)
        return dict(found)
