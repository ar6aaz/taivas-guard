"""Downloads, verifies and stores one IFIC file. Nothing becomes visible until every check passes."""
import hashlib
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import httpx

from ..config import Settings

_MDB_MAGIC = (b"Standard Jet DB", b"Standard ACE DB")
MDB_SUFFIXES = (".mdb", ".accdb")


class BadArtifact(Exception):
    """The download is incomplete, malformed or larger than allowed."""


@dataclass(frozen=True)
class Stored:
    mdb_path: Path
    sha256: str
    size_bytes: int
    etag: str | None = None


def _extract_mdb(zip_path: Path, out_dir: Path, max_bytes: int) -> Path:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            if archive.testzip() is not None:
                raise BadArtifact("A file inside the zip failed its CRC check")
            members = [m for m in archive.infolist() if m.filename.lower().endswith(MDB_SUFFIXES)]
            if len(members) != 1:
                raise BadArtifact(f"Expected one database in the zip, found {len(members)}")
            member = members[0]
            if member.file_size > max_bytes:
                raise BadArtifact(f"The database is {member.file_size} bytes, over the {max_bytes} limit")
            # keep only the base name, so a crafted path cannot write outside out_dir
            target = out_dir / PurePosixPath(member.filename.replace("\\", "/")).name
            written = 0
            with archive.open(member) as src, target.open("wb") as dst:
                while chunk := src.read(1024 * 1024):  # count real bytes: the declared size can lie
                    written += len(chunk)
                    if written > max_bytes:
                        raise BadArtifact("The extracted database is over the size limit")
                    dst.write(chunk)
    except zipfile.BadZipFile as exc:
        raise BadArtifact(f"Not a valid zip: {exc}") from exc
    _check_mdb(target)
    return target


def _check_mdb(path: Path) -> None:
    with path.open("rb") as f:
        if f.read(32)[4:19] not in _MDB_MAGIC:
            raise BadArtifact(f"{path.name} is not an Access database")


def _store(source: Path, domain: str, issue_no: int, settings: Settings, etag: str | None, prepare) -> Stored:
    """Moves a verified file into place at data/ific/<domain>/<issue>/<sha12>/."""
    digest = hashlib.sha256()
    with source.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    sha = digest.hexdigest()
    size = source.stat().st_size
    final = settings.data_dir / "ific" / domain / str(issue_no) / sha[:12]
    if not final.exists():
        final.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=final.parent, prefix=".tmp-") as tmp:
            work = Path(tmp) / "new"
            work.mkdir()
            prepare(work)
            shutil.copy2(source, work / source.name)
            work.rename(final)  # atomic: readers see all of it or none of it
    mdb = next(p for p in final.iterdir() if p.suffix.lower() in MDB_SUFFIXES)
    return Stored(mdb, sha, size, etag)


def store_zip(zip_path: Path, domain: str, issue_no: int, settings: Settings, etag: str | None = None) -> Stored:
    def prepare(work: Path) -> None:
        _extract_mdb(zip_path, work, settings.max_mdb_bytes)

    return _store(zip_path, domain, issue_no, settings, etag, prepare)


def store_mdb(mdb_path: Path, domain: str, issue_no: int, settings: Settings) -> Stored:
    if mdb_path.stat().st_size > settings.max_mdb_bytes:
        raise BadArtifact(f"{mdb_path.name} is over the {settings.max_mdb_bytes} byte limit")
    _check_mdb(mdb_path)
    return _store(mdb_path, domain, issue_no, settings, None, lambda _work: None)


def store_file(path: Path, domain: str, issue_no: int, settings: Settings) -> Stored:
    """Stores an IFIC given as a zip or as a bare Access database."""
    suffix = path.suffix.lower()
    if suffix == ".zip":
        return store_zip(path, domain, issue_no, settings)
    if suffix in MDB_SUFFIXES:
        return store_mdb(path, domain, issue_no, settings)
    raise BadArtifact(f"{path.name}: expected a .zip, .mdb or .accdb file")


def parse_issue_filename(name: str) -> tuple[str, int] | None:
    """ITU's own names say what a file is: ific3074 is space, brific3076 is terrestrial."""
    found = re.fullmatch(r"(br)?ific[ _-]?(\d{4})\.(zip|mdb|accdb)", name.strip(), re.I)
    if not found:
        return None
    return ("terrestrial" if found.group(1) else "space"), int(found.group(2))


def download(client: httpx.Client, url: str, domain: str, issue_no: int, settings: Settings) -> Stored | None:
    """Returns None when the file does not exist yet (404)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.data_dir, prefix=".dl-") as tmp:
        target = Path(tmp) / PurePosixPath(url.split("?")[0]).name
        # ITU omits Content-Length if the client accepts compression, which would skip the length check
        with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
            if response.status_code == 404:
                return None
            response.raise_for_status()
            expected = int(response.headers.get("content-length", 0)) or None
            if expected and expected > settings.max_zip_bytes:
                raise BadArtifact(f"{url} is {expected} bytes, over the limit")
            received = 0
            with target.open("wb") as out:
                for chunk in response.iter_bytes(1024 * 1024):
                    received += len(chunk)
                    if received > settings.max_zip_bytes:
                        raise BadArtifact(f"{url} grew past the size limit")
                    out.write(chunk)
            etag = response.headers.get("etag")
        if response.headers.get("content-encoding") is None and expected and received != expected:
            raise BadArtifact(f"{url}: got {received} bytes, expected {expected}")
        return store_zip(target, domain, issue_no, settings, etag)
