import type { Confidence, Severity } from '../api/types';

export const SEVERITY_RULES = [
  { severity: 'High', rule: 'Margin of 10 dB or more, and overlaps at least one Finnish receiver' },
  { severity: 'Medium', rule: 'Margin of 3 dB or more, and overlaps at least one Finnish receiver. Also: ITU lists Finland' },
  { severity: 'Low', rule: 'Margin under 3 dB, or no overlap with a listed Finnish receiver' },
  { severity: 'Unscored', rule: 'Calculation has not run yet' },
];

export function scorePfd(marginDb: number, stationCount: number): { severity: Severity; reasons: string[] } {
  const reasons = [`${marginDb.toFixed(1)} dB above the limit at peak gain`];
  if (stationCount === 0) {
    reasons.push('No overlap with a station in the Traficom list');
    return { severity: 'low', reasons };
  }
  reasons.push(`Overlaps ${stationCount} Finnish receiver${stationCount === 1 ? '' : 's'} in the Traficom list`);
  if (marginDb >= 10) return { severity: 'high', reasons };
  if (marginDb >= 3) return { severity: 'medium', reasons };
  return { severity: 'low', reasons };
}

/** Confidence is never high until antenna pointing data is available. */
export function confidencePfd(angleDeg: number, orbit: string | null, altitudeKm: number | null): { confidence: Confidence; reasons: string[] } {
  const reasons = ['Peak antenna gain assumed; the free file has no beam pointing data'];
  if (angleDeg < 5) {
    reasons.push(`Very low arrival angle (${angleDeg.toFixed(1)}°): the satellite barely clears Finland's horizon`);
    return { confidence: 'low', reasons };
  }
  if (orbit === 'NGSO') {
    reasons.push(altitudeKm === null ? 'Altitude not filed clearly' : `Worst filed orbit used (${altitudeKm} km), not necessarily the operational one`);
    return { confidence: 'low', reasons };
  }
  return { confidence: 'medium', reasons };
}

export const SEVERITY_ORDER: Record<Severity, number> = { high: 0, medium: 1, low: 2, unscored: 3 };
