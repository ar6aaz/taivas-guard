"""Content-addressed IFIC imports and bounded candidate queries."""

import csv
import hashlib
import importlib.metadata
import json
import logging
import math
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .frequency import FrequencyIndex
from .normalization import normalize
from .readers import REQUIRED, TABLES, canonical_json, read_mdb, station_rows

LOG = logging.getLogger(__name__)
FORMAT_VERSION = "ific-cache-1"

DDL = """
CREATE TABLE IF NOT EXISTS ific_source (
    source_key VARCHAR PRIMARY KEY, sha256 VARCHAR NOT NULL, kind VARCHAR NOT NULL,
    issue_no INTEGER, file_path VARCHAR NOT NULL, reader_version VARCHAR NOT NULL,
    options_json JSON NOT NULL, row_count BIGINT NOT NULL, tables_json JSON NOT NULL,
    imported_at TIMESTAMP DEFAULT current_timestamp
);
CREATE TABLE IF NOT EXISTS ific_raw (
    source_key VARCHAR NOT NULL, table_name VARCHAR NOT NULL, row_no BIGINT NOT NULL, raw JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS ific_assignment (
    source_key VARCHAR NOT NULL, assignment_id VARCHAR NOT NULL, notice_id VARCHAR NOT NULL,
    domain VARCHAR NOT NULL, f_low_mhz DOUBLE, f_high_mhz DOUBLE, adm VARCHAR,
    ntc_type VARCHAR, direction VARCHAR, station_class VARCHAR, fin_listed BOOLEAN NOT NULL,
    gso_lon_deg DOUBLE, latitude_deg DOUBLE, longitude_deg DOUBLE, raw_hash VARCHAR NOT NULL,
    review_reason VARCHAR, publication_action VARCHAR, PRIMARY KEY(source_key, assignment_id)
);
ALTER TABLE ific_assignment ADD COLUMN IF NOT EXISTS publication_action VARCHAR;
CREATE TABLE IF NOT EXISTS ific_station (
    source_key VARCHAR NOT NULL, row_no BIGINT NOT NULL, station_id VARCHAR,
    f_low_mhz DOUBLE, f_high_mhz DOUBLE, latitude_deg DOUBLE, longitude_deg DOUBLE,
    usable BOOLEAN NOT NULL, review_reason VARCHAR, raw JSON NOT NULL,
    PRIMARY KEY(source_key, row_no)
);
"""
RAW_TYPES = {"source_key": "VARCHAR", "table_name": "VARCHAR", "row_no": "BIGINT", "raw": "JSON"}
STATION_TYPES = dict(zip(
    ("source_key", "row_no", "station_id", "f_low_mhz", "f_high_mhz", "latitude_deg", "longitude_deg", "usable", "review_reason", "raw"),
    ("VARCHAR", "BIGINT", "VARCHAR", "DOUBLE", "DOUBLE", "DOUBLE", "DOUBLE", "BOOLEAN", "VARCHAR", "JSON"),
))


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class ImportResult:
    source_key: str
    sha256: str
    cache_hit: bool
    row_count: int
    table_counts: dict
    elapsed_s: float


@dataclass(frozen=True)
class Candidate:
    source_key: str
    station_source_key: str
    assignment_id: str
    notice_id: str
    adm: str | None
    domain: str
    f_low_mhz: float | None
    f_high_mhz: float | None
    matched_assignment_count: int | None
    fin_listed: bool
    publication_action: str | None
    review_reasons: str
    distance_lower_bound_km: float | None
    gap_mhz: float
    max_terrestrial_distance_km: float | None
    screen_version: str = "1"


def _valid_range(low, high):
    return low is not None and high is not None and math.isfinite(low) and math.isfinite(high) and 0 <= low < high


def _gso_visible(longitude_deg):
    """Conservative visibility from a rectangle enclosing Finland.

    Latitude 59 N and longitude 19..32 E deliberately extend outside Finland.
    Unknown/invalid longitude must be reviewed, never excluded by this test.
    """
    if longitude_deg is None or not math.isfinite(longitude_deg) or not -180 <= longitude_deg <= 180:
        return None
    distance_deg = min(abs((longitude_deg - lon + 180) % 360 - 180) for lon in (19, 32))
    if 19 <= longitude_deg <= 32:
        distance_deg = 0
    return math.cos(math.radians(59)) * math.cos(math.radians(distance_deg)) > 6378 / 42164


def _distance_lower_bound_km(latitude_deg, longitude_deg):
    """Minimum spherical distance to an enclosing rectangle, not the real border.

    The rectangle 59..71 N, 19..32 E contains Finland. Thus this value cannot
    overstate distance to Finland. It is safe for an explicitly chosen radius
    prefilter, while retaining extra neighbouring-country stations.
    """
    if (latitude_deg is None or longitude_deg is None or
            not math.isfinite(latitude_deg) or not math.isfinite(longitude_deg) or
            not -90 <= latitude_deg <= 90 or not -180 <= longitude_deg <= 180):
        return None
    gap = 0 if 19 <= longitude_deg <= 32 else min(abs((longitude_deg-lon+180) % 360-180) for lon in (19, 32))
    phi = math.radians(latitude_deg)
    a, b = math.sin(phi), math.cos(phi) * math.cos(math.radians(gap))
    low, high = math.radians(59), math.radians(71)
    optimum = max(low, min(high, math.atan2(a, b)))
    dot = max(a * math.sin(lat) + b * math.cos(lat) for lat in (low, high, optimum))
    return 6371 * math.acos(max(-1.0, min(1.0, dot)))


class AnalysisStore:
    def __init__(self, path, *, threads=None, read_only=False):
        path = str(path)
        if path != ":memory:" and not read_only:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        if threads is not None and threads < 1:
            raise ValueError("threads must be positive")
        self.connection = duckdb.connect(path, read_only=read_only, config={} if threads is None else {"threads": threads})
        self.connection.execute("SET enable_progress_bar=false")
        if not read_only:
            self.connection.execute(DDL)
        self._index_key = None
        self._frequency_index = None

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _identity(self, path, kind, issue_no, reader_version, options):
        sha = file_hash(path)
        identity = [FORMAT_VERSION, sha, kind, issue_no, reader_version, options]
        key = hashlib.sha256(canonical_json(identity).encode()).hexdigest()
        return key, sha

    def _cached(self, key, sha, start):
        row = self.connection.execute("SELECT row_count, tables_json FROM ific_source WHERE source_key=?", [key]).fetchone()
        return None if row is None else ImportResult(key, sha, True, row[0], json.loads(row[1]), time.perf_counter() - start)

    def _remember(self, key, sha, kind, issue_no, path, reader_version, options, counts):
        self.connection.execute("""INSERT INTO ific_source
            (source_key,sha256,kind,issue_no,file_path,reader_version,options_json,row_count,tables_json)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            [key, sha, kind, issue_no, str(Path(path).resolve()), reader_version,
             canonical_json(options), sum(counts.values()), canonical_json(counts)])

    def _bulk_insert(self, table_name, rows, columns):
        # CSV is a bounded, disposable bridge into DuckDB's bulk reader. Avoid
        # millions of Python SQL INSERT calls or a giant in-memory dataframe.
        with tempfile.TemporaryDirectory(prefix="ific-import-") as folder:
            path = Path(folder) / "rows.csv"
            count = 0
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(columns)
                for row in rows:
                    writer.writerow(row)
                    count += 1
            if count:
                self.connection.read_csv(str(path), columns=columns, header=True).insert_into(table_name)
        return count

    def import_ific(self, path, *, domain, issue_no, reader=None, reader_version=None):
        """Import a decoded issue atomically, or reuse its immutable content cache.

        An injected reader yields (table_name, iterable_of_row_dicts). It must
        supply a reader_version so cache identity follows parser changes too.
        """
        if domain not in TABLES or issue_no < 1:
            raise ValueError("Expected space/terrestrial domain and a positive IFIC issue number")
        if reader is not None and not reader_version:
            raise ValueError("An injected reader requires a reader_version")
        if reader_version is None:
            try:
                reader_version = "access-parser:" + importlib.metadata.version("access-parser")
            except importlib.metadata.PackageNotFoundError as exc:
                raise RuntimeError("Install MDB support: pip install '.[mdb]'") from exc
        start = time.perf_counter()
        key, sha = self._identity(path, domain, issue_no, reader_version, {})
        cached = self._cached(key, sha, start)
        if cached:
            return cached
        self.connection.execute("BEGIN TRANSACTION")
        counts = {}
        try:
            for name, rows in (reader or read_mdb)(Path(path), domain):
                name = name.casefold()
                if name not in TABLES[domain] or name in counts:
                    raise ValueError(f"Unexpected or repeated source table: {name}")
                table_start = time.perf_counter()
                values = ((key, name, row_no, canonical_json(raw)) for row_no, raw in enumerate(rows, 1))
                counts[name] = self._bulk_insert("ific_raw", values, RAW_TYPES)
                LOG.info("Stored decoded %s: %d rows in %.3fs", name, counts[name], time.perf_counter() - table_start)
            missing = REQUIRED[domain] - counts.keys()
            if missing:
                raise ValueError(f"Missing required source tables: {sorted(missing)}")
            normalize(self.connection, key, domain)
            if file_hash(path) != sha:
                raise ValueError("Source changed during import; retry with an immutable input file")
            self._remember(key, sha, domain, issue_no, path, reader_version, {}, counts)
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return ImportResult(key, sha, False, sum(counts.values()), counts, time.perf_counter() - start)

    def import_stations(self, path, *, sheet_name="Frequencies of interest"):
        start = time.perf_counter()
        reader_version = "stations-1:openpyxl:" + importlib.metadata.version("openpyxl")
        options = {"sheet_name": sheet_name}
        key, sha = self._identity(path, "stations", None, reader_version, options)
        cached = self._cached(key, sha, start)
        if cached:
            return cached
        self.connection.execute("BEGIN TRANSACTION")
        try:
            rows = ((key, row_no, *normalized, canonical_json(raw))
                    for row_no, raw, normalized in station_rows(path, sheet_name=sheet_name))
            count = self._bulk_insert("ific_station", rows, STATION_TYPES)
            if not count:
                raise ValueError("Station input contains no records")
            if file_hash(path) != sha:
                raise ValueError("Station source changed during import")
            self._remember(key, sha, "stations", None, path, reader_version, options, {"stations": count})
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return ImportResult(key, sha, False, count, {"stations": count}, time.perf_counter() - start)

    def _kind(self, source_key):
        row = self.connection.execute("SELECT kind FROM ific_source WHERE source_key=?", [source_key]).fetchone()
        if row is None:
            raise ValueError(f"Unknown source key: {source_key}")
        return row[0]

    def station_stats(self, source_key):
        if self._kind(source_key) != "stations":
            raise ValueError("Expected a national station source")
        row = self.connection.execute("""SELECT count(*), count(*) FILTER (WHERE usable),
            count(*) FILTER (WHERE review_reason='unknown_receiver_frequency')
            FROM ific_station WHERE source_key=?""", [source_key]).fetchone()
        return dict(zip(("records", "usable_receiver_assignments", "unknown_frequency_records"), row))

    def _index(self, source_key):
        if source_key != self._index_key:
            values = self.connection.execute("""SELECT f_low_mhz, f_high_mhz FROM ific_station
                WHERE source_key=? AND usable""", [source_key]).fetchnumpy()
            self._frequency_index = FrequencyIndex(values["f_low_mhz"], values["f_high_mhz"])
            self._index_key = source_key
        return self._frequency_index

    def screen(self, source_key, station_source_key, *, gap_mhz=0.0, max_terrestrial_distance_km=None, batch_size=10000):
        """Yield frequency candidates, not interference or compliance verdicts."""
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if max_terrestrial_distance_km is not None and (not math.isfinite(max_terrestrial_distance_km) or max_terrestrial_distance_km < 0):
            raise ValueError("max_terrestrial_distance_km must be finite and nonnegative")
        if self._kind(source_key) not in TABLES:
            raise ValueError("Expected an IFIC source")
        stats = self.station_stats(station_source_key)
        index = self._index(station_source_key)
        # Validate even an empty issue or one with only malformed frequencies.
        index.count([], [], gap_mhz=gap_mhz)
        cursor = self.connection.cursor()
        try:
            cursor.execute("""SELECT assignment_id,notice_id,adm,domain,f_low_mhz,f_high_mhz,
                fin_listed,ntc_type,direction,station_class,gso_lon_deg,review_reason,publication_action,latitude_deg,longitude_deg
                FROM ific_assignment WHERE source_key=? AND (
                    (domain='space' AND (fin_listed OR ((direction='E' OR direction IS NULL)
                        AND (ntc_type IN ('G','N') OR ntc_type IS NULL)))) OR
                    (domain='terrestrial' AND (adm IS NULL OR adm<>'FIN')
                        AND (station_class IN ('FB','ML','FP','FL') OR station_class IS NULL)))
                AND (fin_listed OR publication_action IS NULL OR publication_action NOT IN
                     ('SUPPRESS','SUP','S','W','WITHDRAW','WITHDRAWN'))
                ORDER BY assignment_id""", [source_key])
            while rows := cursor.fetchmany(batch_size):
                valid = [i for i, row in enumerate(rows) if _valid_range(row[4], row[5])]
                counts = dict(zip(valid, index.count([rows[i][4] for i in valid], [rows[i][5] for i in valid], gap_mhz=gap_mhz)))
                for i, row in enumerate(rows):
                    aid, nid, adm, domain, low, high, fin, orbit, direction, cls, longitude, reason, action, latitude, station_longitude = row
                    reasons = [reason] if reason else []
                    distance = _distance_lower_bound_km(latitude, station_longitude) if domain == "terrestrial" else None
                    if domain == "terrestrial":
                        if distance is None:
                            reasons.append("unknown_terrestrial_location")
                        elif max_terrestrial_distance_km is not None and distance > max_terrestrial_distance_km and not fin:
                            continue
                    if i not in counts:
                        reasons.append("unknown_assignment_frequency")
                    if stats["unknown_frequency_records"]:
                        reasons.append("incomplete_national_frequencies")
                    visible = _gso_visible(longitude) if orbit == "G" else True
                    if not fin and visible is False:
                        continue
                    if visible is None:
                        reasons.append("unknown_gso_longitude")
                    matched = int(counts[i]) if i in counts else None
                    if fin or matched or reasons:
                        if domain == "space" and (orbit is None or direction is None):
                            reasons.append("incomplete_space_metadata")
                        if adm is None or (domain == "terrestrial" and cls is None):
                            reasons.append("incomplete_administration_or_class")
                        yield Candidate(source_key, station_source_key, aid, nid, adm, domain,
                                        low, high, matched, fin, action, ";".join(reasons),
                                        distance, gap_mhz, max_terrestrial_distance_km)
        finally:
            cursor.close()

    def matches(self, source_key, station_source_key, assignment_id, *, gap_mhz=0.0, batch_size=10000):
        """Stream exact matching receiver identities for one candidate on demand."""
        self.station_stats(station_source_key)
        FrequencyIndex([], []).count([], [], gap_mhz=gap_mhz)
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        row = self.connection.execute("""SELECT f_low_mhz, f_high_mhz FROM ific_assignment
            WHERE source_key=? AND assignment_id=?""", [source_key, assignment_id]).fetchone()
        if row is None or not _valid_range(*row):
            raise ValueError("Assignment not found or frequency range requires review")
        lt, gt = ("<", ">") if gap_mhz == 0 else ("<=", ">=")
        cursor = self.connection.cursor()
        try:
            cursor.execute(f"""SELECT row_no,station_id,f_low_mhz,f_high_mhz FROM ific_station
                WHERE source_key=? AND usable AND f_low_mhz {lt} ? AND f_high_mhz {gt} ?
                ORDER BY row_no""", [station_source_key, row[1] + gap_mhz, row[0] - gap_mhz])
            while rows := cursor.fetchmany(batch_size):
                yield from rows
        finally:
            cursor.close()

    def changes(self, previous_key, current_key, *, complete_snapshots=False):
        """IFIC publications are deltas: absence is not a withdrawal.

        Trust explicit ADD/MODIFY/SUPPRESS (or A/M/S/W) actions. Unknown new
        entries are 'published'; disappearing entries are 'not_republished'.
        Only callers supplying complete cumulative snapshots may opt into
        inferring new/withdrawn from presence or absence.
        """
        previous_kind, current_kind = self._kind(previous_key), self._kind(current_key)
        if previous_kind != current_kind or current_kind not in TABLES:
            raise ValueError("Change tracking requires two IFIC sources in the same domain")
        if previous_key == current_key:
            return
        cursor = self.connection.cursor()
        try:
            cursor.execute("""WITH a AS (SELECT * FROM ific_assignment WHERE source_key=?),
                b AS (SELECT * FROM ific_assignment WHERE source_key=?)
                SELECT coalesce(a.assignment_id,b.assignment_id),
                       CASE WHEN ? THEN CASE WHEN a.assignment_id IS NULL THEN 'new'
                                             WHEN b.assignment_id IS NULL THEN 'withdrawn' ELSE 'modified' END
                            WHEN b.publication_action IN ('SUPPRESS','SUP','S','W','WITHDRAW','WITHDRAWN') THEN 'withdrawn'
                            WHEN b.publication_action IN ('ADD','A') THEN 'new'
                            WHEN b.publication_action IN ('MODIFY','MOD','M','N') THEN 'modified'
                            WHEN a.assignment_id IS NULL THEN CASE WHEN ? THEN 'new' ELSE 'published' END
                            WHEN b.assignment_id IS NULL THEN CASE WHEN ? THEN 'withdrawn' ELSE 'not_republished' END
                            ELSE 'modified' END
                FROM a FULL JOIN b ON a.assignment_id=b.assignment_id
                WHERE a.raw_hash IS DISTINCT FROM b.raw_hash OR
                      (NOT ? AND b.publication_action IN ('SUPPRESS','SUP','S','W','WITHDRAW','WITHDRAWN','ADD','A','MODIFY','MOD','M','N'))
                ORDER BY 1""", [previous_key, current_key, complete_snapshots, complete_snapshots, complete_snapshots, complete_snapshots])
            while rows := cursor.fetchmany(10000):
                yield from rows
        finally:
            cursor.close()

    def export_assignments(self, source_key, path):
        if self._kind(source_key) not in TABLES:
            raise ValueError("Expected an IFIC source")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection.sql("SELECT * FROM ific_assignment WHERE source_key=?", params=[source_key]).write_parquet(str(path), compression="zstd")
