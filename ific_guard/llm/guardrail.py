"""Checks generated text against its evidence: the case and the retrieved passages.

Every number, date and citation in the text must appear in the evidence, and the text must not
claim more than the case shows. Anything unrecognised fails closed, because the cost of a false
alarm is only that the expert gets the template. Membership alone would pass a number that appears
for another reason, so a calculated value, limit or margin must also match its own label, and the
reference data of every draft is rendered by code and never by the model.
"""
import calendar
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel

_NUMBER = re.compile(r"(?:^|(?<=[\s(=:]))[-−+]?\d+(?:[.,]\d+)*|\d+(?:[.,]\d+)*", re.M)
_NUMBER_WORDS = {
    **dict(
        zip(
            "two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
            "seventeen eighteen nineteen twenty".split(),
            range(2, 21),
        )
    ),
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000, "million": 1000000, "billion": 1000000000,
    "twice": 2, "double": 2, "triple": 3, "half": 0.5, "dozen": 12,
}  # "one" is left out: it is nearly always a pronoun
_NUMBER_WORD = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.I)

_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DAY_MONTH_YEAR = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH})\.?,?\s+(\d{{4}})\b", re.I)
_MONTH_DAY_YEAR = re.compile(rf"\b({_MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)
_MONTH_YEAR = re.compile(rf"\b({_MONTH})\.?\s+(\d{{4}})\b", re.I)

_CLAUSE_NUMBER = r"\d{1,2}\.\d{1,3}[A-Z]{0,2}(?:\.\d+)?"
_CLAUSE_LIST = rf"{_CLAUSE_NUMBER}(?:(?:\s*,\s*|\s+(?:to|and|or)\s+)(?:Nos?\.\s*)?{_CLAUSE_NUMBER})*"
_CLAUSE_MENTION = re.compile(rf"\b(?:Nos?\.?|Numbers?|provisions?|provision\(s\)|RR)\s*({_CLAUSE_LIST})", re.I)
_BARE_RR_CLAUSE = re.compile(r"\b(\d{1,2}\.\d{1,3}[A-Z]{1,2})\b")  # 9.21C, 9.52A; a plain 9.14 falls to the number check
_CITATION_FORMS = {
    "article": r"\bArt(?:icle|\.)?s?\s*(\d+[A-Z]?)",
    "appendix": r"\b(?:Appendix|Appendices|App\.|AP)\s*(\d+[A-Z]?)",
    "annex": r"\bAnnex\s*(\d+)",
    "table": r"\bTable\s*(\d+(?:-\d+[A-Z]*)?)",
    "section": r"\b(?:Sub-)?[Ss]ection\s*((?-i:[IVX]+[A-Z]?)|\d+(?:\.\d+)*)\b",
    "paragraph": r"§\s*(\d+)",
    "resolution": r"\bResolutions?\s*(\d+)",
    "recommendation": r"\bRecommendations?\s+((?:ITU-R\s+)?[A-Z][\w./-]*\d[\w./-]*)",
    "tr": r"\bT/R\s*(\d+-\d+)",
    "ecc": r"\b((?:ECC|ERC)/(?:DEC|REC)/?\s*\(?\s*\d+\s*\)?\s*\d*(?:-\d+)?)",
    "report": r"\b((?:ECC|ERC|CEPT) Report\s*\d+)",
    "itu_r": r"\bITU-R\s*([A-Z]+\.\s*\d+(?:-\d+)?)",
    "footnote": r"\bfootnote\s*(\d+(?:\.\d+)*[A-Z]?)",
    "wrc": r"\b(?:WRC)-(\d+)",
    "eu_act": r"\b(?:Decision|Directive|Regulation)\s*(?:\(EU\)\s*|No\.?\s*)?(\d+/\d+(?:/[A-Z]+)?)",
}
_CITATION_PATTERNS = {kind: re.compile(pattern, re.I) for kind, pattern in _CITATION_FORMS.items()}
_DOCUMENT_NAMES = {
    "RR": re.compile(r"\bRadio Regulations\b|\bRR\b"),
    "M4": re.compile(r"\bM4\b|\b4\s*AG/2026M\b|\bFrequency Allocation Table\b"),
    "T/R 25-08": re.compile(r"\bT/R\s*25-08\b"),
}

_ALWAYS_FORBIDDEN = re.compile(
    r"\b(violat\w*|breach\w*|infring\w*|contraven\w*|unlawful\w*|illegal\w*|illicit\w*|unauthori[sz]ed|non-?complian\w*"
    r"|(?:does|do|did) not comply|fails? to comply|failed to comply)\b",
    re.I,
)
_LIMIT_CLAIM = re.compile(
    r"\b(exceed\w*|in excess of|surpass\w*|goes? beyond|above the (?:\w+ )?(?:limit|threshold|mask|trigger)"
    r"|over the (?:\w+ )?(?:limit|threshold|mask|trigger)|prohibited|not permitted|not allowed|forbidden"
    r"|(?:will|does|do|is causing|causes?|caused) (?:\w+ )?(?:harmful )?interfer\w*)\b",
    re.I,
)
_LEVEL_UNIT = re.compile(r"\d\s*(?:dB\w*|W/m)", re.I)
_LABELLED_LEVEL = re.compile(
    r"\b(margin|limit|value)\b(?:(?!margin|limit|value)[^\d\n.;]){0,40}?([-−+]?\d+(?:\.\d+)?)\s*dB", re.I
)
_ADDRESS = re.compile(r"https?://|www\.|[\w.+-]+@[\w-]+\.\w+", re.I)


class Violation(BaseModel):
    kind: str  # number | date | citation | claim | label | address | empty
    value: str
    why: str


def _normal_number(token: str) -> str:
    sign = "-" if token[0] in "-−" else ""
    digits = token.lstrip("-−+")
    if re.fullmatch(r"\d{1,3}(,\d{3})+", digits):
        digits = digits.replace(",", "")
    elif re.fullmatch(r"\d+,\d+", digits):
        digits = digits.replace(",", ".")
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return sign + digits  # a dotted clause number such as 9.52.1
    return sign + format(value.normalize(), "f")


def numbers_in(text: str) -> set[str]:
    found = {_normal_number(m.group()) for m in _NUMBER.finditer(text)}
    found |= {_normal_number(str(_NUMBER_WORDS[m.group(1).lower()])) for m in _NUMBER_WORD.finditer(text)}
    return found


def dates_in(text: str) -> set[str]:
    """Dates as YYYY-MM-DD, or YYYY-MM when the text gives no day."""
    found = {f"{y}-{m}-{d}" for y, m, d in _ISO_DATE.findall(text)}
    for day, month, year in _DAY_MONTH_YEAR.findall(text):
        found.add(f"{year}-{_MONTHS[month.lower()]:02d}-{int(day):02d}")
    for month, day, year in _MONTH_DAY_YEAR.findall(text):
        found.add(f"{year}-{_MONTHS[month.lower()]:02d}-{int(day):02d}")
    remainder = _DAY_MONTH_YEAR.sub(" ", _MONTH_DAY_YEAR.sub(" ", text))
    for month, year in _MONTH_YEAR.findall(remainder):
        found.add(f"{year}-{_MONTHS[month.lower()]:02d}")
    return found


def citations_in(text: str) -> set[str]:
    """Normalised citation keys such as 'rr:9.14', 'table:21-4' and 'doc:M4'."""
    found: set[str] = set()
    for match in _CLAUSE_MENTION.finditer(text):
        found |= {f"rr:{n}" for n in re.findall(_CLAUSE_NUMBER, match.group(1))}
    found |= {f"rr:{n}" for n in _BARE_RR_CLAUSE.findall(text)}
    for kind, pattern in _CITATION_PATTERNS.items():
        for value in pattern.findall(text):
            key = re.sub(r"\s+", "", value).lower()
            found.add(f"{kind}:{key}")
    for name, pattern in _DOCUMENT_NAMES.items():
        if pattern.search(text):
            found.add(f"doc:{name}")
    return found


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _strings(item)
    elif value is not None and not isinstance(value, bool):
        text = str(value)
        if not re.fullmatch(r"[0-9a-fA-F]{32,}", text):  # digit runs inside a file hash are not evidence
            yield text


@dataclass
class Evidence:
    """What generated text may draw on."""

    numbers: set[str] = field(default_factory=set)
    dates: set[str] = field(default_factory=set)
    citations: set[str] = field(default_factory=set)

    def add(self, *values: Any) -> "Evidence":
        for text in _strings(values):
            self.numbers |= numbers_in(text)
            self.dates |= dates_in(text)
            self.citations |= citations_in(text)
        return self

    def add_provisions(self, provisions: Iterable[str]) -> "Evidence":
        for provision in provisions:
            number = str(provision).split("/")[0].strip()
            self.citations.add(f"rr:{number}")
            self.citations.add(f"rr:{str(provision).strip()}")
            self.citations.add(f"section:{number}")
            self.numbers |= numbers_in(str(provision))
            self.citations.add("doc:RR")
        return self


def check_sources(text: str, evidence: Evidence) -> list[Violation]:
    violations = []
    for number in sorted(numbers_in(text) - evidence.numbers):
        violations.append(Violation(kind="number", value=number, why="Not in the case or the retrieved passages."))
    for found in sorted(dates_in(text)):
        allowed = found in evidence.dates or any(d.startswith(found) for d in evidence.dates)
        if not allowed:
            violations.append(Violation(kind="date", value=found, why="Not in the case or the retrieved passages."))
    for citation in sorted(citations_in(text) - evidence.citations):
        violations.append(Violation(kind="citation", value=citation, why="Not in the case or the retrieved passages."))
    return violations


def check_claims(text: str, calculated: bool, margin_db: float | None) -> list[Violation]:
    """A screening case is a possible concern. Only a calculation over the limit can say a limit is passed."""
    violations = [
        Violation(kind="claim", value=m.group(), why="A draft does not decide that a regulation was broken; the expert does.")
        for m in _ALWAYS_FORBIDDEN.finditer(text)
    ]
    limit_shown = calculated and margin_db is not None and margin_db > 0
    if not limit_shown:
        why = "No calculation shows a limit passed, so this can only be a possible concern."
        violations += [Violation(kind="claim", value=m.group(), why=why) for m in _LIMIT_CLAIM.finditer(text)]
    if not calculated:
        why = "A screening case has no calculated level to quote."
        violations += [Violation(kind="claim", value=m.group(), why=why) for m in _LEVEL_UNIT.finditer(text)]
    return violations


def check_labels(text: str, levels: dict[str, float]) -> list[Violation]:
    """A number written as the margin, limit or value must be that one, with its sign."""
    violations = []
    for match in _LABELLED_LEVEL.finditer(text):
        label, written = match.group(1).lower(), match.group(2)
        if label in levels and _normal_number(written) != _normal_number(str(levels[label])):
            why = f"The case gives {levels[label]} dB for the {label}."
            violations.append(Violation(kind="label", value=f"{label} {written} dB", why=why))
    return violations


def check_text(
    text: str, evidence: Evidence, calculated: bool, margin_db: float | None, levels: dict[str, float] | None = None
) -> list[Violation]:
    if not text.strip():
        return [Violation(kind="empty", value="", why="The model returned no text.")]
    violations = check_sources(text, evidence) + check_claims(text, calculated, margin_db)
    violations += check_labels(text, levels or {})
    violations += [
        Violation(kind="address", value=m.group(), why="Drafts carry no addresses or links; the expert adds any recipient.")
        for m in _ADDRESS.finditer(text)
    ]
    return violations
