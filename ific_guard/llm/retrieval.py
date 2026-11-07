"""Retrieval over a small clause index. Selection is exact and deterministic: the case's own
provisions, sections, bands and method pick the passages, so the same case always gets the same text."""
import json
import logging
import os
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

log = logging.getLogger(__name__)

DEFAULT_REFDOCS_DIR = Path(__file__).resolve().parents[2] / "data" / "refdocs"

# A coordination request is answered under 9.52; silence has the 9.52C effect for 9.11-9.14 and 9.21.
_SILENCE_MEANS_UNAFFECTED = ("9.11", "9.11A", "9.12", "9.12A", "9.13", "9.14", "9.21")
_GSO_COORDINATION = ("9.7", "9.7A", "9.7B")


class Passage(BaseModel):
    id: str
    doc: str
    clause: str
    page_or_url: str
    text: str
    provision: str | None = None
    low_mhz: float | None = None
    high_mhz: float | None = None
    tags: list[str] = []


def refdocs_dir() -> Path:
    configured = os.environ.get("IFIC_REFDOCS_DIR")  # the container keeps data/ on a volume, so it copies these elsewhere
    return Path(configured) if configured else DEFAULT_REFDOCS_DIR


@lru_cache
def _load(path: Path) -> tuple[Passage, ...]:
    if not path.exists():
        log.warning("reference index %s is missing; drafts will carry no retrieved passages", path)
        return ()
    with path.open(encoding="utf-8") as f:
        return tuple(Passage(**json.loads(line)) for line in f if line.strip())


def load_index() -> tuple[Passage, ...]:
    return _load(refdocs_dir() / "passages.jsonl")


def provision_number(value: str) -> str:
    """'9.21/C' and '9.1/IA' keep their RR number; the suffix is the BR's own marker."""
    return value.split("/")[0].strip()


def wanted_provisions(details: dict[str, Any]) -> list[str]:
    provisions = [provision_number(str(p)) for p in details.get("provisions", [])]
    sections = [provision_number(str(s)) for s in details.get("sections", [])]
    wanted = list(provisions)
    if "9.1" in sections:  # advance publication: comments go under 9.3
        wanted.append("9.3")
    if "9.6" in sections:  # coordination request: disagreement goes under 9.52, comments under its footnote
        wanted += ["9.52", "9.52.1"]
    if any(p in _SILENCE_MEANS_UNAFFECTED for p in provisions):
        wanted += ["9.52", "9.52C"]
    if "9.14" in provisions:
        wanted.append("9.52A")
    if any(p in _GSO_COORDINATION for p in provisions):
        wanted += ["9.41", "9.51", "9.52"]
    if str(details.get("method", "")).startswith("pfd"):
        wanted += ["21.16", "21.17"]
    return list(dict.fromkeys(wanted))


def retrieve(domain: str, details: dict[str, Any], index: Iterable[Passage] | None = None) -> tuple[list[Passage], list[str]]:
    """Returns the passages for a case and the provisions or bands it names that the index lacks."""
    passages = list(load_index() if index is None else index)
    by_provision = {p.provision: p for p in passages if p.provision}
    by_band = {(p.low_mhz, p.high_mhz): p for p in passages if p.low_mhz is not None}
    found: list[Passage] = []
    missing: list[str] = []

    for number in wanted_provisions(details):
        if number in by_provision:
            found.append(by_provision[number])
        else:
            missing.append(f"RR No. {number}")
    for band in details.get("bands", []):
        key = (band.get("low_mhz"), band.get("high_mhz"))
        if key in by_band:
            found.append(by_band[key])
        else:
            missing.append(f"M4 {key[0]:g}-{key[1]:g} MHz")
    if domain == "terrestrial" or details.get("method") == "p1546":
        found += [p for p in passages if "terrestrial" in p.tags]
    if "9.14" in {provision_number(str(p)) for p in details.get("provisions", [])}:
        found += [p for p in passages if "coordination" in p.tags]
    return list({p.id: p for p in found}.values()), missing
