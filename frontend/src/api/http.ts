// Talks to the IFIC Guard services through the /api proxy. Their shapes are defined in
// ific_guard/schemas.py and the Case.details contract in .scratch/ific-guard-delivery/map.md.
import { SEVERITY_ORDER } from '../lib/severity';
import type { Api, BandMatch, NewRun, Run, CalculatedResult, Domain, DomainFreshness, Finding, FunnelStage, IssueSummary, MonitorStatus, ReferenceSource, ReviewState, ScreenStatus, Severity, SyncRun } from './types';

export interface ArtifactOut { id: number; domain: string; issue_no: number; published_on: string | null; source_url: string; sha256: string; size_bytes: number; fetched_at: string; screen_status: string; screened_at: string | null }
export interface SyncRunOut { id: number; trigger: string; started_at: string; finished_at: string | null; status: string; discovered: number; fetched: number; errors: string[] }
export interface CaseSummary { id: number; domain: string; rule_id: string; severity: string; title: string; adm: string | null; network: string | null; first_issue: number; last_issue: number; comment_deadline: string | null; review_status: string }
export interface CaseOut extends CaseSummary { notice_ids: string[]; details: Record<string, unknown>; source_sha256: string; review_note: string | null; created_at: string; updated_at: string }
export interface UploadOut { artifact: ArtifactOut; new: boolean }
export interface StatusOut { running: boolean; schedule?: string | null; next_run_in_s?: number | null; domains: Record<string, { latest_issue: number | null; published_on: string | null; age_days: number | null; stale: boolean }> }

type Service = 'sync' | 'screening';
const LABEL: Record<Service, string> = { sync: 'sync', screening: 'screening' };

export class ApiError extends Error {
  constructor(message: string, readonly status: number | null) { super(message); }
}

/** Turns a failed response into something a reviewer can act on. */
export function errorMessage(service: Service, status: number, detail: string | null): string {
  if (status === 401 || status === 403) return 'Saving is switched off. Set IFIC_API_KEY for the frontend server to match the services, then restart it.';
  if (status === 409) return detail ?? 'That’s already running.';
  if (status === 404) return detail ?? 'That wasn’t found.';
  if (status >= 500) return `The ${LABEL[service]} service isn’t responding. Check that it’s running on its usual port.`;
  return detail ?? `The ${LABEL[service]} service rejected the request (${status}).`;
}

async function call<T>(service: Service, path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/${service}${path}`, init);
  } catch {
    throw new ApiError(`The ${LABEL[service]} service can’t be reached.`, null);
  }
  if (!res.ok) {
    let detail: string | null = null;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === 'string') detail = body.detail;
    } catch { /* an empty proxy error has no body */ }
    throw new ApiError(errorMessage(service, res.status, detail), res.status);
  }
  return (await res.json()) as T;
}

const RULE_LABELS: Record<string, string> = {
  'fin-named-by-br': 'ITU names Finland',
  'm4-no-space-to-earth': 'No space-to-Earth allocation in Finland',
  'station-rx-overlap': 'Overlaps a Finnish receiver',
};
export const ruleLabel = (id: string) => RULE_LABELS[id] ?? id;

const EVALUATED = new Set(['space-3074', 'terrestrial-3076']);
const issueKey = (domain: string, issue: number) => `${domain}-${issue}`;
const asSeverity = (s: string): Severity => (s === 'high' || s === 'medium' || s === 'low' ? s : 'unscored');
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : []);
const num = (v: unknown): number | null => (typeof v === 'number' ? v : null);
// Downloads store a URL; imports and uploads store import:name or upload:name.
export const fileName = (source: string) => source.replace(/^(import|upload):/, '').split('/').pop() ?? source;
const fileOf = (name: string, sha: string) => ({ zip: name, zipSha256: sha, mdb: '', mdbSha256: '' });

const LETTERS = 'ific-guard-letters-v1';
function readLetters(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(LETTERS) ?? '{}') as Record<string, string>; } catch { return {}; }
}
function saveLetter(id: string, text: string) {
  try { localStorage.setItem(LETTERS, JSON.stringify({ ...readLetters(), [id]: text })); } catch { /* the edit lasts until the page closes */ }
}

function reviewOf(status: string, note: string | null, id: string): ReviewState {
  return { status: status === 'accepted' || status === 'rejected' ? status : 'pending', note: note ?? '', letter: readLetters()[id] ?? null, history: [] };
}

const bandLabel = (b: BandMatch) => `${b.lowMhz}–${b.highMhz} MHz`;

export function mapBands(details: Record<string, unknown>): BandMatch[] {
  if (!Array.isArray(details.bands)) return [];
  return (details.bands as Record<string, unknown>[]).map((b) => ({
    lowMhz: Number(b.low_mhz), highMhz: Number(b.high_mhz), primaryServices: String(b.m4_primary_services ?? ''), finnishUse: String(b.finnish_use ?? ''),
    passive: b.passive === true, pages: String(b.m4_table_pages ?? ''), groupCount: Array.isArray(b.group_ids) ? b.group_ids.length : 0,
    stationIds: strings(b.station_ids), classes: (b.classes as Record<string, number>) ?? {},
  }));
}

function mapResult(d: Record<string, unknown>): CalculatedResult | null {
  if (typeof d.method !== 'string' || d.method === 'screening') return null;
  return {
    method: d.method, inputs: (d.calc_inputs as Record<string, unknown>) ?? {}, valueDb: num(d.value_db), limitDb: num(d.limit_db), marginDb: num(d.margin_db),
    maskId: typeof d.mask_id === 'string' ? d.mask_id : null, worstPoint: (d.worst_point as Record<string, unknown>) ?? null,
  };
}

function mapCitations(d: Record<string, unknown>): string[] {
  if (!Array.isArray(d.citations)) return [];
  return (d.citations as Record<string, unknown>[]).map((c) => [c.doc, c.clause, c.page_or_url].filter(Boolean).join(', '));
}

function base(c: CaseSummary, issueId: string) {
  const id = String(c.id);
  return {
    id, issueId, domain: c.domain as Domain, issue: c.last_issue, firstIssue: c.first_issue, lastIssue: c.last_issue, kind: c.rule_id, kindLabel: ruleLabel(c.rule_id),
    title: c.network ?? c.title, summary: c.title, adm: c.adm ?? 'Unknown', orbit: null, provision: null, severity: asSeverity(c.severity), windowDeadline: c.comment_deadline,
    confidence: null, confidenceReasons: [], stations: [], stationIds: [], stationClasses: {}, calc: null, terrestrial: null,
  };
}

/** List rows carry no details, so anything from `details` stays empty until the case is opened. */
export function mapSummary(c: CaseSummary, issueId: string): Finding {
  return {
    ...base(c, issueId), band: null, sourceId: '', relatedSourceIds: [], sections: [], coordProvisions: [], severityReasons: [], marginDb: null, stationCount: null, bands: [], visibility: [], basis: null, result: null, assumptions: [], citations: [], evidence: [],
    file: fileOf('', ''), updatedAt: null, review: reviewOf(c.review_status, null, String(c.id)),
  };
}

export function mapCase(c: CaseOut, issueId: string, sourceName: string): Finding {
  const d = c.details ?? {};
  const bands = mapBands(d);
  const result = mapResult(d);
  const provisions = strings(d.provisions);
  const [first, ...rest] = c.notice_ids;
  return {
    ...base(c, issueId),
    band: bands.length === 1 ? bandLabel(bands[0]) : bands.length > 1 ? `${bands.length} bands` : null,
    sourceId: first ?? '', relatedSourceIds: rest, sections: strings(d.sections), coordProvisions: provisions,
    severityReasons: [c.title], marginDb: result?.marginDb ?? null, stationCount: Array.isArray(d.station_ids) ? d.station_ids.length : null,
    stationIds: strings(d.station_ids), stationClasses: (d.classes as Record<string, number>) ?? {},
    bands, visibility: strings(d.visibility), basis: typeof d.basis === 'string' ? d.basis : null, result, assumptions: strings(d.assumptions), citations: mapCitations(d),
    evidence: [
      { table: 'notice', key: `ntc_id in ${c.notice_ids.join(', ')}` },
      ...(Array.isArray(d.group_ids) ? [{ table: 'grp', key: `${d.group_ids.length} group(s) named in the coordination data` }] : []),
      ...provisions.map((p) => ({ table: 'provn', key: `adm = 'FIN', provision ${p}` })),
    ],
    file: fileOf(sourceName, c.source_sha256), updatedAt: c.updated_at, review: reviewOf(c.review_status, c.review_note, String(c.id)),
  };
}

export function rankFindings(rows: Finding[]): Finding[] {
  return [...rows].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] || (a.windowDeadline ?? '9').localeCompare(b.windowDeadline ?? '9') || a.title.localeCompare(b.title));
}

/** A case stays open across issues, so it belongs to every stored issue in its first-to-last range. */
export const caseInIssue = (c: CaseSummary, domain: string, issue: number) => c.domain === domain && c.first_issue <= issue && issue <= c.last_issue;

const GAPS: Record<Domain, string[]> = {
  space: ['No pfd check yet, so a case can’t say a limit is exceeded', 'No beam coverage check, so the M4 rule over-flags by design', 'M4 band file merges 3400–4200 MHz, which can miss cases in 3.4–3.8 GHz', 'AP30, AP30A and AP30B aren’t screened', 'Station overlap compares frequency only, not location, power or antenna direction'],
  terrestrial: ['Land-mobile notices from the configured neighbours only, within an approximate 300 km screen', 'P.1546 without terrain; T/R 25-08 thresholds stop at 470 MHz and are unconfirmed on the Russian border', 'Notices that could not be calculated are listed for expert review; they are not clear results'],
};

// An artifact screened before terrestrial rules existed keeps this status until it is screened again.
const NO_RULES_GAPS = ['No rules ran on this stored file', 'This does not mean there are no concerns'];

export function summariseIssue(a: ArtifactOut, cases: CaseSummary[]): IssueSummary {
  const domain = a.domain as Domain;
  const mine = cases.filter((c) => caseInIssue(c, a.domain, a.issue_no));
  const severityCounts: Record<Severity, number> = { high: 0, medium: 0, low: 0, unscored: 0 };
  const rules = new Map<string, number>();
  for (const c of mine) {
    severityCounts[asSeverity(c.severity)]++;
    rules.set(c.rule_id, (rules.get(c.rule_id) ?? 0) + 1);
  }
  const ruleCounts: FunnelStage[] = [...rules.entries()].sort((x, y) => y[1] - x[1]).map(([key, n]) => ({ key, label: ruleLabel(key), n }));
  const deadlines = mine.map((c) => c.comment_deadline).filter((d): d is string => !!d).sort();
  const id = issueKey(a.domain, a.issue_no);
  return {
    id, artifactId: a.id, domain, issue: a.issue_no, releasedOn: a.published_on, screenStatus: a.screen_status as ScreenStatus, screenedAt: a.screened_at,
    commentDeadline: deadlines[deadlines.length - 1] ?? null, file: fileOf(fileName(a.source_url), a.sha256), sourceUrl: a.source_url, evaluationIssue: EVALUATED.has(id),
    counts: [{ label: 'Cases', value: mine.length }, ...ruleCounts.map((r) => ({ label: r.label, value: r.n }))], funnel: [], ruleCounts, severityCounts,
    brListedNetworks: rules.get('fin-named-by-br') ?? 0, limitations: a.screen_status === 'no_rules' ? NO_RULES_GAPS : GAPS[domain] ?? [],
  };
}

const allCases = () => call<CaseSummary[]>('screening', '/cases?limit=1000');
const newestPerIssue = (rows: ArtifactOut[]) => { // the API lists newest first; a republished issue keeps only its latest revision
  const seen = new Set<string>();
  return rows.filter((a) => { const k = issueKey(a.domain, a.issue_no); if (seen.has(k)) return false; seen.add(k); return true; });
};
const artifacts = async () => newestPerIssue(await call<ArtifactOut[]>('sync', '/issues?limit=500'));

export const toSyncRun = (r: SyncRunOut): SyncRun => ({ id: r.id, trigger: r.trigger === 'schedule' ? 'Scheduled' : r.trigger === 'manual' ? 'Manual' : r.trigger, startedAt: r.started_at, finishedAt: r.finished_at, status: r.status, discovered: r.discovered, fetched: r.fetched, errors: r.errors });
// The API returns naive UTC timestamps; mark them so the browser doesn't read them as local time.
const utc = (t: string) => (/[zZ]|[+-]\d\d:\d\d$/.test(t) ? t : `${t}Z`);

// Sends the file with progress, which fetch can't report.
function uploadFile(input: NewRun, onProgress?: (fraction: number) => void): Promise<UploadOut> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('file', input.file);
    form.append('domain', input.domain);
    if (input.issue) form.append('issue_no', String(input.issue));
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/sync/issues/upload');
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress?.(e.loaded / e.total); };
    xhr.onerror = () => reject(new ApiError('The sync service can’t be reached.', null));
    xhr.onload = () => {
      let body: { detail?: unknown } | null = null;
      try { body = JSON.parse(xhr.responseText) as { detail?: unknown }; } catch { /* an empty proxy error has no body */ }
      if (xhr.status >= 200 && xhr.status < 300 && body) resolve(body as unknown as UploadOut);
      else reject(new ApiError(errorMessage('sync', xhr.status, typeof body?.detail === 'string' ? body.detail : null), xhr.status));
    };
    xhr.send(form);
  });
}

export function runFromArtifact(a: ArtifactOut, message = ''): Run {
  const status = a.screen_status === 'failed' ? 'failed' : a.screen_status === 'pending' ? 'running' : 'succeeded';
  const done = a.screen_status === 'no_rules' ? 'The file is stored, but no rules exist for terrestrial issues yet. This does not mean there are no concerns.' : 'Screening finished.';
  return {
    id: `issue-${a.id}`, trigger: 'on_demand', domain: a.domain as Domain, issue: a.issue_no, fileName: fileName(a.source_url), sha256: a.sha256, sizeBytes: a.size_bytes, startedAt: utc(a.fetched_at),
    status, stageIndex: status === 'running' ? 1 : status === 'failed' ? 1 : 3, issueId: issueKey(a.domain, a.issue_no),
    message: status === 'failed' ? 'Screening failed. Check the screening service’s log, then try screening it again.' : `${message}${done}`,
  };
}

const SOURCES: ReferenceSource[] = [
  { name: 'M4 band summary', version: 'generetated-files/m4_satellite_filter.csv', detail: '212 bands from 30 MHz up. Merges 3400–4200 MHz, so it can miss cases in 3.4–3.8 GHz.', url: null, status: 'partial' },
  { name: 'ITU coordination data', version: 'Each IFIC’s provn tables', detail: 'Finds filings where ITU names Finland.', url: null, status: 'loaded' },
  { name: 'Traficom frequencies of interest', version: 'v05112026 (synthetic)', detail: 'Compares receive frequency only. Stations with no receive frequency are skipped.', url: null, status: 'partial' },
  { name: 'ITU Radio Regulations limits', version: '2024 edition', detail: 'The pfd engine and limits are in the code, but no screening rule uses them yet, so no limit is checked.', url: 'https://www.itu.int/pub/r-reg-rr', status: 'planned' },
  { name: 'ECC T/R 25-08 and ITU-R P.1546', version: 'June 2025; P.1546-6', detail: 'Terrestrial rules not added yet.', url: 'https://docdb.cept.org/document/909', status: 'planned' },
];

export const httpApi: Api = {
  mode: 'live',
  capabilities: { upload: true, checkItuNow: true, rescreen: true, reviewStatuses: ['accepted', 'rejected', 'pending'], letterStorage: 'browser' },
  severityNote: 'Set by the screening service. These are placeholders until the planned ranking replaces them.',
  listSeverityRules: async () => [
    { severity: 'High', rule: 'ITU names Finland in the filing’s coordination data, or a visible satellite transmits in a passive band with no space-to-Earth allocation' },
    { severity: 'Medium', rule: 'A visible satellite transmits in a band with Finnish terrestrial use and no space-to-Earth allocation, or on an RX or TX band of a station in Traficom’s list' },
  ],
  listIssues: async () => {
    const [rows, cases] = await Promise.all([artifacts(), allCases()]);
    return rows.map((a) => summariseIssue(a, cases));
  },
  getMonitor: async (): Promise<MonitorStatus> => {
    const [status, runs] = await Promise.all([call<StatusOut>('sync', '/status'), call<SyncRunOut[]>('sync', '/runs?limit=1')]);
    const last = runs[0];
    const domains: DomainFreshness[] = Object.entries(status.domains).map(([domain, d]) => ({ domain: domain as Domain, latestIssue: d.latest_issue, publishedOn: d.published_on, ageDays: d.age_days, stale: d.stale }));
    return { active: true, running: status.running, domains, lastCheck: last ? { at: utc(last.started_at), label: last.trigger === 'schedule' ? 'Scheduled' : 'Manual', status: last.status, errors: last.errors } : null, schedule: status.schedule ?? null, nextCheckInS: status.next_run_in_s ?? null, nextExpectedOn: null, note: null };
  },
  listFindings: async (issueId) => {
    const i = issueId.lastIndexOf('-');
    const domain = issueId.slice(0, i);
    const issue = Number(issueId.slice(i + 1));
    const cases = await allCases();
    return rankFindings(cases.filter((c) => caseInIssue(c, domain, issue)).map((c) => mapSummary(c, issueId)));
  },
  getFinding: async (id) => {
    let c: CaseOut;
    try {
      c = await call<CaseOut>('screening', `/cases/${encodeURIComponent(id)}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
    const match = (await call<ArtifactOut[]>('sync', '/issues?limit=500')).find((a) => a.sha256 === c.source_sha256);
    return mapCase(c, issueKey(c.domain, c.last_issue), match ? fileName(match.source_url) : 'Source file');
  },
  updateReview: async (id, change) => {
    if (change.letter !== undefined) saveLetter(id, change.letter); // no letter field in the API yet
    if (change.status) {
      await call<CaseOut>('screening', `/cases/${encodeURIComponent(id)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ review_status: change.status, review_note: change.note || null }),
      });
    }
    const f = await httpApi.getFinding(id);
    if (!f) throw new ApiError('That case no longer exists.', 404);
    return f;
  },
  listRuns: async () => [],
  getRun: async (id) => {
    const artifactId = Number(id.replace(/^issue-/, ''));
    const row = (await call<ArtifactOut[]>('sync', '/issues?limit=500')).find((a) => a.id === artifactId);
    return row ? runFromArtifact(row) : null;
  },
  listSyncRuns: async () => (await call<SyncRunOut[]>('sync', '/runs?limit=50')).map((r) => ({ ...toSyncRun(r), startedAt: utc(r.started_at), finishedAt: r.finished_at ? utc(r.finished_at) : null })),
  findIssueByHash: async (sha) => (await httpApi.listIssues()).find((i) => i.file.zipSha256 === sha) ?? null,
  createRun: async (input, onProgress) => {
    const { artifact, new: isNew } = await uploadFile(input, onProgress);
    return runFromArtifact(artifact, isNew ? '' : 'This exact file was already stored, so nothing new was added. ');
  },
  checkItuNow: async () => { await call('sync', '/sync', { method: 'POST' }); },
  rescreen: async (issue) => { await call('screening', `/issues/${issue.artifactId}/screen`, { method: 'POST' }); },
  listReferenceSources: async () => SOURCES,
  listLimits: async () => [],
};
