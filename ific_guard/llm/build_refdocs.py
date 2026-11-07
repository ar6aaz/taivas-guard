"""Rebuilds data/refdocs/passages.jsonl: short clause excerpts cut from source PDFs the operator supplies.

The full documents are copyrighted and stay out of the repository. Only the clauses named below
are stored, each with its source. Needs PyMuPDF, which is not a runtime dependency:

    uv run --with pymupdf python -m ific_guard.llm.build_refdocs \
        --rr1 rr-vol1.pdf --rr2 rr-vol2.pdf --tr tr2508.pdf --m4 finland-regulations.pdf
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

from ..config import get_settings
from ..screening.rules import load_m4_conflict_bands
from .retrieval import DEFAULT_REFDOCS_DIR, Passage

RR1_URL = "https://search.itu.int/history/HistoryDigitalCollectionDocLibrary/1.49.48.en.101.pdf"
RR2_URL = "https://search.itu.int/history/HistoryDigitalCollectionDocLibrary/1.49.48.en.102.pdf"
TR_URL = "https://docdb.cept.org/download/4789"
M4_URL = "https://www.finlex.fi/fi/viranomaiset/maarayskokoelmat/traficom-viestinta/2026/3"

# Provisions the screening data can name, and the procedural clauses a response depends on.
RR_CLAUSES = (
    "9.3 9.6 9.7 9.7A 9.7B 9.11 9.11A 9.12 9.12A 9.13 9.14 9.21 9.36 9.38 9.41 "
    "9.48 9.49 9.51 9.52 9.52A 9.52C 21.16 21.17"
).split()
RR_FOOTNOTES = ["9.52.1"]
EXCERPT_CHARS = 2000
M4_EXCERPT_CHARS = 320
M4_TABLE_PDF_OFFSET = 9  # the table's own page 170 is PDF page 179

_NEXT_CLAUSE = re.compile(r"^\s*\d{1,2}\.\d{1,3}[A-Z]{0,2}(\s|$)")
_PAGE_END = re.compile(r"^\s*(_{5,}|– ?\d+ ?–|CHAPTER [IVX]+\b|(Sub-)?Section [IVX]+[A-Z]?\b)")
_RANGE = re.compile(r"(\d+(?:\.\d+)?) - (\d+(?:\.\d+)?) (kHz|MHz|GHz)")
_UNIT_MHZ = {"kHz": 1e-3, "MHz": 1.0, "GHz": 1e3}


def _flat(lines: list[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def rr_clause(pages: list[str], clause: str) -> tuple[int, str] | None:
    """Clause text up to the next clause number, footnote rule or page furniture."""
    start = re.compile(rf"^\s*{re.escape(clause)}\s*$")
    for number, text in enumerate(pages, 1):
        if number < 20:  # table of contents
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if start.match(line):
                body: list[str] = []
                for following in lines[i + 1 :]:
                    if _NEXT_CLAUSE.match(following) or _PAGE_END.match(following):
                        break
                    body.append(following)
                if body:
                    return number, _flat(body)[:EXCERPT_CHARS]
    return None


def rr_footnote(pages: list[str], clause: str) -> tuple[int, str] | None:
    start = re.compile(rf"^\s*(?:\d+\s+)?{re.escape(clause)}\b\s*(.*)$")
    for number, text in enumerate(pages, 1):
        lines = text.splitlines()
        rule = next((i for i, line in enumerate(lines) if line.strip().startswith("_____")), None)
        if rule is None:
            continue
        for i, line in enumerate(lines[rule:], rule):
            if match := start.match(line):
                body = [match.group(1)]
                for following in lines[i + 1 :]:
                    if re.match(r"^\s*\d+\s+\d+\.\d+", following) or _PAGE_END.match(following):
                        break
                    body.append(following)
                return number, _flat(body)[:EXCERPT_CHARS]
    return None


def rr_passages(pages: list[str], url: str) -> tuple[list[Passage], list[str]]:
    out, missing = [], []
    for clause in RR_CLAUSES + RR_FOOTNOTES:
        found = rr_footnote(pages, clause) if clause in RR_FOOTNOTES else rr_clause(pages, clause)
        if not found:
            missing.append(f"RR No. {clause}")
            continue
        number, text = found
        article = clause.split(".")[0]
        out.append(
            Passage(
                id=f"rr-{clause}",
                doc="RR",
                clause=f"Article {article}, No. {clause}",
                page_or_url=f"Radio Regulations, edition of 2024, Vol. 1, PDF p. {number}, {url}",
                text=text,
                provision=clause,
            )
        )
    return out, missing


def ap5_passages(pages: list[str], url: str) -> tuple[list[Passage], list[str]]:
    """How Appendix 5 identifies the administrations to coordinate with. Its thresholds belong to the masks data."""
    for number, text in enumerate(pages, 1):
        flat = _flat(text.splitlines())
        match = re.search(r"APPENDIX 5 \(REV\.WRC-23\) (Identification of administrations.*?which are:)", flat)
        if number > 20 and match:
            return [
                Passage(
                    id="rr-ap5-1",
                    doc="RR",
                    clause="Appendix 5 (Rev.WRC-23), § 1",
                    page_or_url=f"Radio Regulations, edition of 2024, Vol. 2, PDF p. {number}, {url}",
                    text=match.group(1),
                    tags=["coordination"],
                )
            ], []
    return [], ["RR Appendix 5, § 1"]


TR_SECTIONS = [
    # (id, clause label, PDF page, start text, end text, tags)
    ("tr-recommends", "recommends 1 to 7", 4, "recommends", None, ["terrestrial"]),
    ("tr-table-1", "Annex 3, Table 1", 10, "Table 1: Trigger", "For practical purposes", ["terrestrial"]),
    ("tr-table-2", "Annex 4, Table 2", 12, "Table 2:", "A4.4", ["terrestrial"]),
]


def tr_passages(pages: list[str], url: str) -> tuple[list[Passage], list[str]]:
    out, missing = [], []
    for pid, clause, number, start, end, tags in TR_SECTIONS:
        text = _flat(pages[number - 1].splitlines())
        begin = text.find(start)
        stop = text.find(end, begin + 1) if end else len(text)
        if begin < 0 or stop < 0:
            missing.append(f"T/R 25-08 {clause}")
            continue
        out.append(
            Passage(
                id=pid,
                doc="T/R 25-08",
                clause=clause,
                page_or_url=f"ECC Recommendation T/R 25-08, edition 6 June 2025, p. {number}, {url}",
                text=text[begin:stop].strip()[:EXCERPT_CHARS],
                tags=tags,
            )
        )
    return out, missing


def m4_passages(pages: list[str], url: str) -> tuple[list[Passage], list[str]]:
    """One short row excerpt per band the rule uses, found by exact band edges on the band's table page."""
    out, missing = [], []
    settings = get_settings()
    for band in load_m4_conflict_bands(settings.m4_filter_path):
        first, last = (int(p) for p in band.pages.split("-"))
        found = False
        for table_page in range(first, last + 1):
            pdf_page = table_page + M4_TABLE_PDF_OFFSET
            text = _flat(pages[pdf_page - 1].splitlines())
            for match in _RANGE.finditer(text):
                factor = _UNIT_MHZ[match.group(3)]
                if (
                    abs(float(match.group(1)) * factor - band.low_mhz) < 1e-6
                    and abs(float(match.group(2)) * factor - band.high_mhz) < 1e-6
                ):
                    out.append(
                        Passage(
                            id=f"m4-{band.low_mhz:g}-{band.high_mhz:g}",
                            doc="M4",
                            clause=f"Frequency Allocation Table, {band.low_mhz:g}-{band.high_mhz:g} MHz",
                            page_or_url=f"Regulation 4 AG/2026M, table p. {table_page} (PDF p. {pdf_page}), {url}",
                            text=text[match.start() : match.start() + M4_EXCERPT_CHARS].split("Mode of traffic")[0].strip(),
                            low_mhz=band.low_mhz,
                            high_mhz=band.high_mhz,
                        )
                    )
                    found = True
                    break
            if found:
                break
        if not found:
            missing.append(f"M4 {band.low_mhz:g}-{band.high_mhz:g} MHz (table p. {band.pages})")
    return out, missing


def _read(path: Path) -> tuple[list[str], str]:
    import pymupdf

    with pymupdf.open(path) as document:
        pages = [page.get_text() for page in document]
    return pages, hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("--rr1", "--rr2", "--tr", "--m4"):
        parser.add_argument(flag, type=Path, required=True)
    parser.add_argument("--out", type=Path, default=DEFAULT_REFDOCS_DIR)
    args = parser.parse_args()

    rr1, rr1_sha = _read(args.rr1)
    rr2, rr2_sha = _read(args.rr2)
    tr, tr_sha = _read(args.tr)
    m4, m4_sha = _read(args.m4)

    passages: list[Passage] = []
    missing: list[str] = []
    for found, gaps in (
        rr_passages(rr1, RR1_URL),
        ap5_passages(rr2, RR2_URL),
        tr_passages(tr, TR_URL),
        m4_passages(m4, M4_URL),
    ):
        passages += found
        missing += gaps

    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "passages.jsonl").open("w", encoding="utf-8") as f:
        for passage in passages:
            f.write(passage.model_dump_json(exclude_none=True) + "\n")
    manifest = {
        "note": "Short excerpts only. Fetch the full documents from the URLs below; they are not stored here.",
        "obtained": [
            {"doc": "RR", "edition": "2024, Vol. 1 (Articles)", "url": RR1_URL, "sha256": rr1_sha},
            {"doc": "RR", "edition": "2024, Vol. 2 (Appendices)", "url": RR2_URL, "sha256": rr2_sha},
            {"doc": "T/R 25-08", "edition": "6 June 2025", "url": TR_URL, "sha256": tr_sha},
            {"doc": "M4", "edition": "4 AG/2026M, in force 31.3.2026", "url": M4_URL, "sha256": m4_sha},
        ],
        "not_obtained": [
            "ECC decisions named in ARCHITECTURE.md 4.8: not fetched; M4 excerpts name some of them, but their text is not indexed.",
            "BR Special Sections (the printed comment expiry date): not available in the free IFIC database.",
        ],
        "clauses_not_found": missing,
        "passages": len(passages),
    }
    (args.out / "sources.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passages": len(passages), "clauses_not_found": missing}, indent=2))


if __name__ == "__main__":
    main()
