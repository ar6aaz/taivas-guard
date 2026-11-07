import { describe, expect, it } from 'vitest';
import s3074 from '../data/space-3074.json';
import s3083 from '../data/space-3083.json';
import { pfdSteps } from './pfd';
import { confidencePfd, scorePfd } from './severity';
import { daysUntil } from './format';

describe('pfdSteps', () => {
  it('matches the hand-worked RLNEUTRON-1 example', () => {
    const r = pfdSteps({ powerDensityDbwHz: -50.2, peakPowerDbw: 1.5, refBandwidthHz: 4000, gainDbi: 5.6, distanceKm: 160 });
    expect(r.densityInRef).toBeCloseTo(-14.18, 1);
    expect(r.spreading).toBeCloseTo(115.07, 1);
    expect(r.pfd).toBeCloseTo(-123.65, 1);
    expect(r.cappedByPeak).toBe(false);
  });
  it('caps at total power when the signal is narrower than the reference bandwidth', () => {
    const r = pfdSteps({ powerDensityDbwHz: -30, peakPowerDbw: 2, refBandwidthHz: 1e6, gainDbi: 0, distanceKm: 1000 });
    expect(r.cappedByPeak).toBe(true);
    expect(r.power).toBe(2);
  });
  it('agrees with every exported sample result', () => {
    for (const f of [...s3074.findings, ...s3083.findings]) {
      const c = f.calc;
      const r = pfdSteps({ powerDensityDbwHz: c.powerDensityDbwHz, peakPowerDbw: c.peakPowerDbw, refBandwidthHz: c.refBandwidthHz, gainDbi: c.gainDbi, distanceKm: c.distanceKm });
      expect(Math.abs(r.pfd - c.pfd)).toBeLessThan(0.2);
      expect(Math.abs(c.pfd - c.limit - c.marginDb)).toBeLessThan(0.11);
    }
  });
});

describe('severity', () => {
  it('needs a Finnish receiver before rating anything above low', () => {
    expect(scorePfd(38, 0).severity).toBe('low');
    expect(scorePfd(20, 170).severity).toBe('high');
    expect(scorePfd(5, 3).severity).toBe('medium');
    expect(scorePfd(1, 3).severity).toBe('low');
  });
  it('never reports high confidence, and marks horizon cases low', () => {
    expect(confidencePfd(0.2, 'GSO', null).confidence).toBe('low');
    expect(confidencePfd(15, 'GSO', null).confidence).toBe('medium');
    expect(confidencePfd(90, 'NGSO', 160).confidence).toBe('low');
  });
});

describe('daysUntil', () => {
  it('counts calendar days to the deadline', () => {
    expect(daysUntil('2027-02-27', new Date('2026-11-07T12:00:00Z'))).toBe(112);
    expect(daysUntil('2026-10-23', new Date('2026-11-07T12:00:00Z'))).toBe(-15);
  });
});
