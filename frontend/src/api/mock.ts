// Sample-mode backend. Serves real results from an exploratory screen of real
// ITU files (see scripts/export_sample.py), and only simulates new runs.
import s3083 from '../data/space-3083.json';
import s3074 from '../data/space-3074.json';
import t3083 from '../data/terrestrial-3083.json';
import t3076 from '../data/terrestrial-3076.json';
import { confidencePfd, scorePfd, SEVERITY_ORDER, SEVERITY_RULES } from '../lib/severity';
import { PIPELINE_STAGES } from './types';
import type { Api, Domain, Finding, FunnelStage, IssueSummary, LimitRow, MonitorStatus, NewRun, PfdCalculation, ReferenceSource, ReviewState, Run, Severity, SourceFile, StationRef, TerrestrialDetail } from './types';

const PFD_ASSUMPTIONS = ['Free-space propagation (RR No. 21.16)', 'Maximum notified antenna gain pointed at Finland', 'Worst filed orbit and worst emission in the group', 'Angle of arrival from a coarse set of points across Finland'];
const SPACE_LIMITS = ['Only seven Table 21-4 limit rows are implemented, so other bands aren’t checked', 'No beam pointing data in the free file, so false alarms are expected', 'Satellites that only receive and earth stations aren’t calculated', 'AP30/30A/30B filings aren’t in the free file', 'Adjacent-band effects aren’t checked', 'Finnish receivers come from Traficom’s synthetic list'];
const TERR_LIMITS = ['P.1546 field strength hasn’t been calculated, so nothing here is scored', 'Distance to Finland uses a rough set of border points', 'Only land-mobile station classes are included', 'Comparison with the previous issue isn’t implemented; the file’s own ADD/MODIFY/SUPPRESS labels are shown'];

interface RawBase { ntcId: number; network: string; adm: string; orbit: string; provision: string; sections: string[]; actCode: string; targetNtcId: number | null; band: string; stationCount: number }
interface RawPfd extends RawBase { calc: PfdCalculation; stations: StationRef[] }
interface RawBr extends RawBase { coordProvisions: string[]; agreeStatus: string[]; directions: string[]; groupCount: number }
interface SpaceRaw {
  issue: number; date: string; deadline: string; file: SourceFile; funnel: FunnelStage[]; findings: RawPfd[]; brListed: RawBr[];
  counts: { notices: number; byType: Record<string, number>; groups: number; brFinlandNetworks: number };
}
interface TerrRaw {
  issue: number; date: string; file: SourceFile; nearBorder: (TerrestrialDetail & { adm: string })[];
  counts: { assignments: number; byIntent: Record<string, number>; landMobileForeign: number; within100: number; within300: number };
}
const space3083 = s3083 as unknown as SpaceRaw;
const space3074 = s3074 as unknown as SpaceRaw;
const terr3083 = t3083 as unknown as TerrRaw;
const terr3076 = t3076 as unknown as TerrRaw;

const EVAL = new Set(['space-3074', 'terrestrial-3076']);
const blankReview = (): ReviewState => ({ status: 'pending', note: '', letter: null, history: [] });

const STORE = 'ific-guard-sample-v1';
interface Saved { reviews: Record<string, ReviewState>; runs: Run[] }
function loadSaved(): Saved {
  try {
    const raw = localStorage.getItem(STORE);
    if (raw) return JSON.parse(raw) as Saved;
  } catch { /* storage may be unavailable; the app still works for this session */ }
  return { reviews: {}, runs: [] };
}
const saved = loadSaved();
function persist() {
  try { localStorage.setItem(STORE, JSON.stringify(saved)); } catch { /* see above */ }
}

function windowFor(sections: string[], deadline: string): string | null {
  // CR/D, CR/E and API/B are status updates: without CR/C or API/A published
  // on their own there's no new window to act on.
  const opens = sections.includes('CR/C') || sections.includes('API/A');
  const updateOnly = sections.some((s) => ['CR/D', 'CR/E', 'API/B'].includes(s));
  return opens && !updateOnly ? deadline : null;
}

/** ITU's paired 12xxx/32xxx records describe one network, so show it once. */
function mergePairs<T>(rows: T[], key: (r: T) => string, id: (r: T) => number, prefer: (r: T) => boolean, same: (a: T, b: T) => boolean = () => true) {
  const out = new Map<string, { row: T; ids: number[] }>();
  for (const r of rows) {
    const k = key(r);
    const hit = out.get(k);
    if (!hit) out.set(k, { row: r, ids: [id(r)] });
    else {
      hit.ids.push(id(r));
      if (prefer(r) && !prefer(hit.row) && same(r, hit.row)) hit.row = r;
    }
  }
  return [...out.values()];
}

function buildSpace(raw: SpaceRaw): Finding[] {
  const issueId = `space-${raw.issue}`;
  const common = (r: RawPfd | RawBr) => ({ issueId, domain: 'space' as Domain, issue: raw.issue, firstIssue: raw.issue, lastIssue: raw.issue, adm: r.adm, orbit: r.orbit, provision: r.provision, sections: r.sections, file: raw.file, review: blankReview(), terrestrial: null, summary: null, stationIds: [], stationClasses: {}, bands: [], visibility: [], basis: null, result: null, updatedAt: null });
  const pfd = mergePairs(raw.findings, (r) => `${r.network}|${r.calc.fMinMhz}|${r.calc.fMaxMhz}`, (r) => r.ntcId, (r) => r.targetNtcId !== null, (a, b) => a.calc.marginDb === b.calc.marginDb).map(({ row: r, ids }): Finding => {
    const { severity, reasons } = scorePfd(r.calc.marginDb, r.stationCount);
    const conf = confidencePfd(r.calc.angleDeg, r.orbit, r.calc.altitudeKm);
    return {
      ...common(r), id: `${issueId}-pfd-${r.ntcId}-${r.calc.groupId}-${r.band.replace(/\D+/g, '-').replace(/^-|-$/g, '')}`, kind: 'pfd_screen', kindLabel: 'Above a limit', title: r.network, band: `${r.calc.fMinMhz}–${r.calc.fMaxMhz} MHz`, sourceId: String(r.ntcId), relatedSourceIds: [...new Set(ids)].filter((i) => i !== r.ntcId).map(String), coordProvisions: [],
      severity, severityReasons: reasons, confidence: conf.confidence, confidenceReasons: conf.reasons, marginDb: r.calc.marginDb, stationCount: r.stationCount, stations: r.stations, calc: r.calc,
      windowDeadline: windowFor(r.sections, raw.deadline), assumptions: PFD_ASSUMPTIONS, citations: [r.band, 'RR No. 21.16'],
      evidence: [{ table: 'notice', key: `ntc_id = ${r.ntcId}` }, { table: 'grp', key: `grp_id = ${r.calc.groupId}` }, { table: 'emiss', key: `grp_id = ${r.calc.groupId}, design_emi = ${r.calc.emission}` }, { table: 's_beam', key: `ntc_id = ${r.ntcId}, beam_name = ${r.calc.beam}` }],
    };
  });
  const br = mergePairs(raw.brListed, (r) => r.network, (r) => r.ntcId, (r) => r.targetNtcId !== null).map(({ row: r, ids }): Finding => ({
    ...common(r), id: `${issueId}-br-${r.ntcId}`, kind: 'br_listed', kindLabel: 'ITU lists Finland', title: r.network, band: r.band.replace('-', '–'), sourceId: String(r.ntcId), relatedSourceIds: ids.filter((i) => i !== r.ntcId).map(String), coordProvisions: r.coordProvisions,
    severity: 'medium', severityReasons: [`ITU lists Finland under ${r.coordProvisions.join(', ')}`, 'ITU’s list is for information only (RR No. 9.36.1), so an expert still decides'], confidence: 'medium', confidenceReasons: ['Read directly from the filing; no calculation involved'], marginDb: null, stationCount: r.stationCount, stations: [], calc: null,
    windowDeadline: windowFor(r.sections, raw.deadline), assumptions: ['Band is the full range of the listed groups, so station overlap may be overstated'], citations: ['RR No. 9.36.1', ...r.coordProvisions.map((p) => `RR provision ${p}`)],
    evidence: [{ table: 'provn', key: `adm = 'FIN', ${r.groupCount} group(s) of ntc_id ${r.ntcId}` }, { table: 'pub_ssn', key: `ntc_id = ${r.ntcId}` }],
  }));
  return [...pfd, ...br];
}

function buildTerr(raw: TerrRaw): Finding[] {
  const issueId = `terrestrial-${raw.issue}`;
  return raw.nearBorder.map((n): Finding => ({
    id: `${issueId}-${n.terrakey}`, issueId, domain: 'terrestrial', issue: raw.issue, firstIssue: raw.issue, lastIssue: raw.issue, summary: null, stationIds: [], stationClasses: {}, bands: [], visibility: [], basis: null, result: null, updatedAt: null, kind: 'near_border', kindLabel: 'Near the border', title: n.site, adm: n.adm, orbit: null, band: `${n.fMhz} MHz`, sourceId: String(n.terrakey), relatedSourceIds: [], provision: null, sections: [n.noticeType], coordProvisions: [],
    severity: 'unscored', severityReasons: ['P.1546 field strength not calculated yet'], confidence: 'not_calculated', confidenceReasons: ['Only distance and band are known so far'], marginDb: null, stationCount: 0, stations: [], calc: null, terrestrial: { terrakey: n.terrakey, stationClass: n.stationClass, fMhz: n.fMhz, bandwidthKhz: n.bandwidthKhz, intent: n.intent, noticeType: n.noticeType, site: n.site, lat: n.lat, lon: n.lon, borderKm: n.borderKm, powerDbw: n.powerDbw, powerType: n.powerType, gainDbi: n.gainDbi, heightM: n.heightM },
    windowDeadline: null, assumptions: ['Distance is to the nearest of 14 rough border points', 'Strongest listed antenna record shown'], citations: ['ECC T/R 25-08 (criterion to be confirmed)', 'ITU-R P.1546-6 (not run)'],
    evidence: [{ table: 'fxm_terra', key: `terrakey = ${n.terrakey}` }, { table: 'fxm_ant', key: `terrakey = ${n.terrakey}` }], file: raw.file, review: blankReview(),
  }));
}

function rank(rows: Finding[]): Finding[] {
  return [...rows].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] || (b.marginDb ?? -1e9) - (a.marginDb ?? -1e9) || (a.terrestrial?.borderKm ?? 0) - (b.terrestrial?.borderKm ?? 0) || a.title.localeCompare(b.title));
}

const findings = new Map<string, Finding[]>([
  ['space-3083', rank(buildSpace(space3083))], ['space-3074', rank(buildSpace(space3074))],
  ['terrestrial-3083', rank(buildTerr(terr3083))], ['terrestrial-3076', rank(buildTerr(terr3076))],
]);

function severityCounts(id: string): Record<Severity, number> {
  const c: Record<Severity, number> = { high: 0, medium: 0, low: 0, unscored: 0 };
  for (const f of findings.get(id) ?? []) c[f.severity]++;
  return c;
}

function spaceSummary(raw: SpaceRaw): IssueSummary {
  const id = `space-${raw.issue}`;
  const t = raw.counts.byType;
  return {
    id, artifactId: null, screenStatus: 'done', screenedAt: null, sourceUrl: null, ruleCounts: [], domain: 'space', issue: raw.issue, releasedOn: raw.date, commentDeadline: raw.deadline, file: raw.file, evaluationIssue: EVAL.has(id),
    counts: [{ label: 'Filings', value: raw.counts.notices }, { label: 'Geostationary', value: t.G ?? 0 }, { label: 'Non-geostationary', value: t.N ?? 0 }, { label: 'Earth stations', value: t.S ?? 0 }, { label: 'Frequency groups', value: raw.counts.groups }],
    funnel: raw.funnel, severityCounts: severityCounts(id), brListedNetworks: raw.counts.brFinlandNetworks, limitations: SPACE_LIMITS,
  };
}

function terrSummary(raw: TerrRaw): IssueSummary {
  const id = `terrestrial-${raw.issue}`;
  const intent = raw.counts.byIntent;
  return {
    id, artifactId: null, screenStatus: 'done', screenedAt: null, sourceUrl: null, ruleCounts: [], domain: 'terrestrial', issue: raw.issue, releasedOn: raw.date, commentDeadline: null, file: raw.file, evaluationIssue: EVAL.has(id),
    counts: [{ label: 'Assignments', value: raw.counts.assignments }, { label: 'Added', value: intent.ADD ?? 0 }, { label: 'Modified', value: intent.MODIFY ?? 0 }, { label: 'Suppressed', value: intent.SUPPRESS ?? 0 }],
    funnel: [{ key: 'all', label: 'Assignments in the issue', n: raw.counts.assignments }, { key: 'lm', label: 'Foreign land-mobile', n: raw.counts.landMobileForeign }, { key: '300', label: 'Within 300 km of Finland', n: raw.counts.within300 }, { key: '100', label: 'Within 100 km of Finland', n: raw.counts.within100 }],
    severityCounts: severityCounts(id), brListedNetworks: 0, limitations: TERR_LIMITS,
  };
}

const issues: IssueSummary[] = [spaceSummary(space3083), terrSummary(terr3083), spaceSummary(space3074), terrSummary(terr3076)];

const withReview = (f: Finding): Finding => ({ ...f, review: saved.reviews[f.id] ?? f.review });
const SAMPLE_TIME = '2026-11-07T00:07:00Z';

const sampleRuns: Run[] = issues.map((i) => ({
  id: `sample-${i.id}`, trigger: 'sample', domain: i.domain, issue: i.issue, fileName: i.file.zip, sha256: i.file.zipSha256, sizeBytes: null, startedAt: SAMPLE_TIME, status: 'succeeded', stageIndex: PIPELINE_STAGES.length, issueId: i.id,
  message: 'Exploratory screen, run once by hand on the real ITU file',
}));

const STAGE_MS = 1100;
function advance(run: Run): Run {
  if (run.status !== 'running') return run;
  const stage = Math.floor((Date.now() - Date.parse(run.startedAt)) / STAGE_MS);
  if (stage < PIPELINE_STAGES.length) return { ...run, stageIndex: stage };
  return { ...run, status: 'simulated', stageIndex: PIPELINE_STAGES.length, message: 'Simulation finished. The pipeline isn’t connected yet, so this file wasn’t analysed and has no results.' };
}

export const mockApi: Api = {
  mode: 'sample',
  capabilities: { upload: true, checkItuNow: false, rescreen: false, reviewStatuses: ['accepted', 'needs_info', 'rejected'], letterStorage: 'browser' },
  severityNote: 'Starter rules for the demo. Traficom’s experts should set the real ones.',
  listSeverityRules: async () => SEVERITY_RULES,
  listSyncRuns: async () => [],
  checkItuNow: async () => { throw new Error('Not available in sample mode.'); },
  rescreen: async () => { throw new Error('Not available in sample mode.'); },
  listIssues: async () => issues,
  getMonitor: async (): Promise<MonitorStatus> => ({
    active: false, running: false,
    domains: [{ domain: 'space', latestIssue: 3083, publishedOn: '2026-10-27', ageDays: null, stale: false }, { domain: 'terrestrial', latestIssue: 3083, publishedOn: '2026-10-27', ageDays: null, stale: false }],
    lastCheck: { at: '2026-11-07T00:00:00Z', label: 'By hand', status: 'ok', errors: [] }, schedule: null, nextCheckInS: null, nextExpectedOn: '2026-11-10',
    note: 'Sample mode has no monitoring. The latest release was checked by hand on ITU’s download page. The next date assumes the usual two-week cycle.',
  }),
  listFindings: async (issueId) => (findings.get(issueId) ?? []).map(withReview),
  getFinding: async (id) => {
    for (const rows of findings.values()) {
      const hit = rows.find((f) => f.id === id);
      if (hit) return withReview(hit);
    }
    return null;
  },
  updateReview: async (id, change) => {
    const f = await mockApi.getFinding(id);
    if (!f) throw new Error('Finding not found');
    const events = [] as ReviewState['history'];
    const at = new Date().toISOString();
    if (change.status && change.status !== f.review.status) events.push({ at, action: `Status set to ${change.status.replace('_', ' ')}`, note: change.note ?? f.review.note });
    if (change.letter !== undefined && change.letter !== f.review.letter) events.push({ at, action: 'Draft letter edited', note: '' });
    if (change.note !== undefined && change.note !== f.review.note && !change.status) events.push({ at, action: 'Review note updated', note: change.note });
    const next: ReviewState = { status: change.status ?? f.review.status, note: change.note ?? f.review.note, letter: change.letter ?? f.review.letter, history: [...events, ...f.review.history] };
    saved.reviews[id] = next;
    persist();
    return { ...f, review: next };
  },
  listRuns: async () => {
    saved.runs = saved.runs.map(advance);
    persist();
    return [...saved.runs, ...sampleRuns];
  },
  getRun: async (id) => (await mockApi.listRuns()).find((r) => r.id === id) ?? null,
  findIssueByHash: async (sha) => issues.find((i) => i.file.zipSha256 === sha || i.file.mdbSha256 === sha) ?? null,
  createRun: async ({ file, sha256, domain, issue }: NewRun) => {
    const run: Run = { id: `run-${Date.now().toString(36)}`, trigger: 'on_demand', domain, issue, fileName: file.name, sha256, sizeBytes: file.size, startedAt: new Date().toISOString(), status: 'running', stageIndex: 0, issueId: null, message: 'Simulated run' };
    saved.runs = [run, ...saved.runs];
    persist();
    return run;
  },
  listReferenceSources: async (): Promise<ReferenceSource[]> => [
    { name: 'Traficom frequencies of interest', version: 'v05112026 (synthetic)', detail: '874 rows, 872 station numbers, 1.5 MHz to 27.5 GHz. Used for Finnish receiver overlap.', url: null, status: 'loaded' },
    { name: 'Finnish frequency table (M4)', version: '4 AG/2026M, in force 31 Mar 2026', detail: '5,917 rows from the open-data API. Not yet used in the screen.', url: 'https://opendata.traficom.fi/api/v13/Taajuusjakotaulukko', status: 'planned' },
    { name: 'ITU Radio Regulations', version: '2024 edition', detail: 'Seven Table 21-4 rows implemented. Appendix 5 and footnotes aren’t yet.', url: 'https://www.itu.int/pub/r-reg-rr', status: 'partial' },
    { name: 'ECC T/R 25-08', version: '6 June 2025', detail: 'Land-mobile coordination thresholds for the terrestrial check.', url: 'https://docdb.cept.org/document/909', status: 'planned' },
    { name: 'ITU-R P.1546', version: 'P.1546-6', detail: 'Field-strength model for terrestrial findings. Not run yet.', url: 'https://www.itu.int/rec/R-REC-P.1546/en', status: 'planned' },
  ],
  listLimits: async (): Promise<LimitRow[]> => [
    ['RR Table 21-4, 1525–1530 MHz', '4 kHz', '−154 for δ ≤ 5°, rising 0.5 dB/° to −144 at 25°'],
    ['RR Table 21-4, 2025–2110 MHz', '4 kHz', '−154 for δ ≤ 5°, rising 0.5 dB/° to −144 at 25°'],
    ['RR Table 21-4, 2200–2300 MHz', '4 kHz', '−154 for δ ≤ 5°, rising 0.5 dB/° to −144 at 25°'],
    ['RR Table 21-4, 2500–2690 MHz', '1 MHz', '−136 for δ ≤ 5°, rising 0.55 dB/° to −125 at 25°'],
    ['RR Table 21-4, 8025–8500 MHz', '4 kHz', '−150 for δ ≤ 5°, rising 0.5 dB/° to −140 at 25°'],
    ['RR Table 21-4, 17.7–19.3 GHz', '1 MHz', '−115 for δ ≤ 5°, rising 0.5 dB/° to −105 at 25°. X = 0 assumed, which is the most lenient case'],
    ['RR Table 21-4, 19.3–19.7 GHz', '1 MHz', '−115 for δ ≤ 5°, rising 0.5 dB/° to −105 at 25°'],
  ].map(([citation, refBandwidth, rule]) => ({ citation, refBandwidth, rule })),
};
