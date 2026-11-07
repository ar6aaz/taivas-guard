// Re-derives a screen result in the browser so a reviewer can see each step.
// The sample export and this must agree; pfd.test.ts checks that.

export interface PfdInput {
  powerDensityDbwHz: number;
  peakPowerDbw: number | null;
  refBandwidthHz: number;
  gainDbi: number;
  distanceKm: number;
}

export function pfdSteps(i: PfdInput) {
  const densityInRef = i.powerDensityDbwHz + 10 * Math.log10(i.refBandwidthHz);
  const cappedByPeak = i.peakPowerDbw !== null && i.peakPowerDbw < densityInRef;
  const power = cappedByPeak ? (i.peakPowerDbw as number) : densityInRef;
  const spreading = 10 * Math.log10(4 * Math.PI * (i.distanceKm * 1000) ** 2);
  return { densityInRef, cappedByPeak, power, spreading, pfd: power + i.gainDbi - spreading };
}
