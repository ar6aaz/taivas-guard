"""Deterministic finding ranking shared by space and terrestrial rules."""
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ScoringWeights:
    margin: float = 10.0
    special_interest: float = 20.0
    primary_allocation: float = 10.0
    passive_band: float = 30.0
    near_limit: float = 15.0
    envelope_margin_factor: float = 0.5  # an upper-bound margin counts for less than a time-sampled one

    @classmethod
    def from_settings(cls, settings: Any) -> "ScoringWeights":
        return cls(
            settings.pfd_margin_weight, settings.pfd_special_interest_weight,
            settings.pfd_primary_allocation_weight, settings.pfd_passive_band_weight,
            settings.pfd_near_limit_weight, settings.pfd_envelope_margin_factor,
        )


DEFAULT_WEIGHTS = ScoringWeights()


def score_finding(details: Mapping[str, Any], weights: ScoringWeights = DEFAULT_WEIGHTS) -> dict[str, Any]:
    """Return a bounded score and the visible reasons used to produce it."""
    margin = details.get("margin_db")
    score = 0.0
    reasons: list[str] = []
    if details.get("input_flagged"):
        reasons.append("margin not scored: an input is flagged as a data-quality item")
    elif isinstance(margin, (int, float)):
        factor = weights.margin
        if details.get("margin_basis") == "envelope":
            factor *= weights.envelope_margin_factor
            reasons.append(f"margin is an upper bound, not a sampled result × {weights.envelope_margin_factor:g}")
        score += max(-30.0, min(70.0, float(margin) * factor))
        reasons.append(f"margin {float(margin):.2f} dB × {factor:g}")
        if -3 <= margin <= 0:
            score += weights.near_limit
            reasons.append(f"within 3 dB of the limit × {weights.near_limit:g}")
    if details.get("special_interest"):
        score += weights.special_interest
        reasons.append(f"special-interest station × {weights.special_interest:g}")
    if details.get("primary_allocation"):
        score += weights.primary_allocation
        reasons.append(f"M4 primary allocation × {weights.primary_allocation:g}")
    if details.get("passive_band"):
        score += weights.passive_band
        reasons.append(f"passive band × {weights.passive_band:g}")
    return {
        "score": round(max(0.0, min(100.0, score)), 3),
        "reasons": reasons,
        "weights": {
            "margin": weights.margin,
            "special_interest": weights.special_interest,
            "primary_allocation": weights.primary_allocation,
            "passive_band": weights.passive_band,
            "near_limit": weights.near_limit,
            "envelope_margin_factor": weights.envelope_margin_factor,
        },
    }
