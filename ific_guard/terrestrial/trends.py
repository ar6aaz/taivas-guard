"""Reproducible, descriptive summaries of neighbouring terrestrial notices."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..config import get_settings
from ..screening.space import MdbReader
from .adapter import TerrestrialIssue, filter_near_finland


@dataclass(frozen=True)
class TrendObservation:
    """One descriptive observation, with the exact command used to reproduce it."""

    key: str
    text: str
    counts: dict[str, dict[int, int]]
    reproduction: str
    linked_finding_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class NeighbourTrendReport:
    """Read-only trend data and report-ready observations."""

    issue_numbers: tuple[int, ...]
    administrations: tuple[str, ...]
    activity: dict[str, dict[int, int]]
    screened_activity: dict[str, dict[int, int]]
    observations: tuple[TrendObservation, ...]
    assumptions: tuple[str, ...]


def _ordered_issues(issues: Mapping[int, TerrestrialIssue] | Iterable[TerrestrialIssue]) -> list[TerrestrialIssue]:
    values = list(issues.values()) if isinstance(issues, Mapping) else list(issues)
    if len({issue.issue_no for issue in values}) != len(values):
        raise ValueError("Neighbour trend input contains duplicate issue numbers.")
    return sorted(values, key=lambda issue: issue.issue_no)


def _counts_by_issue(
    issues: Sequence[TerrestrialIssue],
    administrations: Sequence[str],
    included: Mapping[int, Iterable],
) -> dict[str, dict[int, int]]:
    return {
        adm: {
            issue.issue_no: sum(notice.adm == adm for notice in included[issue.issue_no])
            for issue in issues
        }
        for adm in administrations
    }


def _frequency_band(frequency_mhz: float | None) -> str:
    if frequency_mhz is None or frequency_mhz <= 0:
        return "unknown"
    low = int(frequency_mhz // 100) * 100
    return f"{low}-{low + 100} MHz"


def _bandwidth_band(bandwidth_khz: float | None) -> str:
    if bandwidth_khz is None or bandwidth_khz < 0:
        return "unknown"
    if bandwidth_khz <= 12.5:
        return "<=12.5 kHz"
    if bandwidth_khz <= 25:
        return "12.5-25 kHz"
    return ">25 kHz"


def _distribution(
    issues: Sequence[TerrestrialIssue],
    administrations: Sequence[str],
    label,
    notices_by_issue: Mapping[int, Iterable],
) -> dict[str, dict[int, int]]:
    values: dict[str, dict[int, int]] = defaultdict(dict)
    for issue in issues:
        counts = Counter(
            label(notice)
            for notice in notices_by_issue[issue.issue_no]
            if notice.adm in administrations
        )
        for category, count in sorted(counts.items()):
            values[category][issue.issue_no] = count
    return {category: dict(counts) for category, counts in sorted(values.items())}


def _format_counts(counts: Mapping[str, Mapping[int, int]], issue_numbers: Sequence[int]) -> str:
    return "; ".join(
        f"{category} " + ", ".join(f"{issue}:{counts.get(issue, 0)}" for issue in issue_numbers)
        for category, counts in counts.items()
    ) or "none"


def analyse_neighbour_trends(
    issues: Mapping[int, TerrestrialIssue] | Iterable[TerrestrialIssue],
    *,
    neighbour_adms: Sequence[str] | None = None,
    distance_limits_km: Mapping[str, float] | None = None,
    reproduction_command: str = "uv run python -m ific_guard.terrestrial.trends",
    reproduction_issues: Sequence[str] | None = None,
) -> NeighbourTrendReport:
    """Aggregate notices without mutating issues or applying any inference."""
    parsed = _ordered_issues(issues)
    settings = get_settings()
    administrations = tuple(neighbour_adms or settings.neighbour_adms)
    limits = distance_limits_km or settings.terrestrial_distance_limits_km
    screened = {
        issue.issue_no: [item.notice for item in filter_near_finland(
            issue.notices,
            neighbour_adms=administrations,
            distance_limits_km=limits,
        )]
        for issue in parsed
    }
    notices = {issue.issue_no: list(issue.notices.values()) for issue in parsed}
    activity = _counts_by_issue(parsed, administrations, notices)
    screened_activity = _counts_by_issue(parsed, administrations, screened)
    distributions = {
        "frequency_band": _distribution(parsed, administrations, lambda notice: _frequency_band(notice.frequency_mhz), screened),
        "station_class": _distribution(parsed, administrations, lambda notice: notice.station_class, screened),
        "bandwidth": _distribution(parsed, administrations, lambda notice: _bandwidth_band(notice.bandwidth_khz), screened),
    }
    issue_text = ", ".join(str(issue.issue_no) for issue in parsed)
    reproduction_text = " ".join(reproduction_issues or (str(issue.issue_no) for issue in parsed))
    observations = (
        TrendObservation(
            "administration-activity",
            f"Neighbour notice activity across {len(parsed)} issues: "
            + "; ".join(
                f"{adm} " + ", ".join(f"{issue}:{activity[adm][issue]}" for issue in (i.issue_no for i in parsed))
                for adm in administrations
            )
            + ". This is descriptive evidence from the compared issues, not a prediction.",
            activity,
            f"{reproduction_command} --observation administration-activity --issues {reproduction_text}",
        ),
        TrendObservation(
            "screened-activity",
            f"Notices passing the configured distance screen (default engineering limit {limits.get('default', 300.0):g} km): "
            + "; ".join(
                f"{adm} " + ", ".join(f"{issue}:{screened_activity[adm][issue]}" for issue in (i.issue_no for i in parsed))
                for adm in administrations
            )
            + ". This is descriptive evidence from the compared issues, not a prediction.",
            screened_activity,
            f"{reproduction_command} --observation screened-activity --issues {reproduction_text}",
        ),
        TrendObservation(
            "frequency-band",
            f"Screened notice frequency-band counts for issues {issue_text}; "
            f"{_format_counts(distributions['frequency_band'], [issue.issue_no for issue in parsed])}. "
            "Categories use 100 MHz bins and are descriptive, not predictive.",
            distributions["frequency_band"],
            f"{reproduction_command} --observation frequency-band --issues {reproduction_text}",
        ),
        TrendObservation(
            "station-class",
            f"Screened station-class counts for issues {issue_text}: "
            f"{_format_counts(distributions['station_class'], [issue.issue_no for issue in parsed])}. "
            "Station classes are retained as notified.",
            distributions["station_class"],
            f"{reproduction_command} --observation station-class --issues {reproduction_text}",
        ),
        TrendObservation(
            "bandwidth",
            f"Screened bandwidth categories for issues {issue_text}: "
            f"{_format_counts(distributions['bandwidth'], [issue.issue_no for issue in parsed])}. "
            "Bins are <=12.5 kHz, 12.5-25 kHz and >25 kHz.",
            distributions["bandwidth"],
            f"{reproduction_command} --observation bandwidth --issues {reproduction_text}",
        ),
    )
    return NeighbourTrendReport(
        tuple(issue.issue_no for issue in parsed),
        administrations,
        activity,
        screened_activity,
        observations,
        (
            "The 300 km distance screen is an engineering approximation, not a regulatory value.",
            "The Finnish border polyline used by the adapter is an engineering approximation, not a legal border or coastline.",
            f"Only the configured administrations were included: {', '.join(administrations)}.",
            "With only the compared issues, observations are weak evidence and make no predictions.",
            "Published Article 11 totals do not fully reconcile to notice rows, so counts may not match them "
            "(see TerrestrialIssue.article11_reconciliation).",
            "The meanings of fxm_coord, action and target are inferred from the data, not confirmed against the ITU ReadMe.",
        ),
    )


def render_neighbour_trends(report: NeighbourTrendReport) -> str:
    """Render the report section consumed by the final evaluation report."""
    lines = ["## Neighbour trends", ""]
    lines.extend(f"- Assumption: {assumption}" for assumption in report.assumptions)
    lines.append("")
    for observation in report.observations:
        lines.extend((
            f"### {observation.key}",
            observation.text,
            f"Reproduce: `{observation.reproduction}`",
            "",
        ))
    return "\n".join(lines).rstrip()


def _load_issues(paths: Sequence[str]) -> list[TerrestrialIssue]:
    issues = []
    for path in paths:
        reader = MdbReader(Path(path))
        issues.append(TerrestrialIssue(reader))
    return issues


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation", choices=("administration-activity", "screened-activity", "frequency-band", "station-class", "bandwidth"))
    parser.add_argument("--issues", nargs="+", required=True, help="Terrestrial MDB files, in any order")
    args = parser.parse_args(argv)
    report = analyse_neighbour_trends(
        _load_issues(args.issues),
        reproduction_issues=tuple(args.issues),
    )
    if args.observation:
        print(next(item.text + "\nReproduce: " + item.reproduction for item in report.observations if item.key == args.observation))
    else:
        print(render_neighbour_trends(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
