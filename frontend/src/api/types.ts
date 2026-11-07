// The contract between this UI and the backend. Units are in field names,
// matching schema.sql. See frontend/README.md for the HTTP endpoints.

export type Domain = 'space' | 'terrestrial';
export type Severity = 'high' | 'medium' | 'low' | 'unscored';
export type Confidence = 'medium' | 'low' | 'not_calculated';
export type ReviewStatus = 'pending' | 'accepted' | 'rejected' | 'needs_info';
export type FindingKind = 'pfd_screen' | 'br_listed' | 'near_border';
export type RunTrigger = 'scheduled' | 'on_demand' | 'sample';
export type RunStatus = 'running' | 'succeeded' | 'failed' | 'simulated';

export interface SourceFile {
  zip: string;
  zipSha256: string;
  mdb: string;
  mdbSha256: string;
}

export interface FunnelStage {
  key: string;
  label: string;
  n: number;
}

export type ScreenStatus = 'pending' | 'done' | 'no_rules' | 'failed';

export interface IssueSummary {
  id: string; // e.g. "space-3083"
  artifactId: number | null;
  domain: Domain;
  issue: number;
  releasedOn: string | null;
  screenStatus: ScreenStatus;
  screenedAt: string | null;
  commentDeadline: string | null;
  file: SourceFile;
  sourceUrl: string | null;
  evaluationIssue: boolean; // named in the Traficom brief
  counts: { label: string; value: number }[];
  funnel: FunnelStage[]; // ordered narrowing, when the source provides one
  ruleCounts: FunnelStage[];
  severityCounts: Record<Severity, number>;
  brListedNetworks: number;
  limitations: string[];
}

export interface PfdCalculation {
  groupId: number;
  beam: string;
  fMinMhz: number;
  fMaxMhz: number;
  emission: string;
  gainDbi: number;
  powerDensityDbwHz: number;
  peakPowerDbw: number | null;
  refBandwidthHz: number;
  distanceKm: number;
  angleDeg: number;
  pfd: number;
  limit: number;
  marginDb: number;
  altitudeKm: number | null;
  inclinationDeg: number | null;
  lat: number | null;
  lon: number | null;
}

export interface StationRef {
  id: number;
  rxMhz: number;
  licence: string | null;
  lat: number;
  lon: number;
}

export interface TerrestrialDetail {
  terrakey: number;
  stationClass: string;
  fMhz: number;
  bandwidthKhz: number | null;
  intent: string;
  noticeType: string;
  site: string;
  lat: number;
  lon: number;
  borderKm: number;
  powerDbw: number | null;
  powerType: string | null;
  gainDbi: number | null;
  heightM: number | null;
}

export interface BandMatch {
  lowMhz: number;
  highMhz: number;
  primaryServices: string;
  finnishUse: string;
  passive: boolean;
  pages: string;
  groupCount: number;
  stationIds: string[];
  classes: Record<string, number>;
}

/** A calculated result in the shape agreed in Case.details. */
export interface CalculatedResult {
  method: string;
  inputs: Record<string, unknown>;
  valueDb: number | null;
  limitDb: number | null;
  marginDb: number | null;
  maskId: string | null;
  worstPoint: Record<string, unknown> | null;
}

export interface Finding {
  id: string;
  issueId: string;
  domain: Domain;
  issue: number;
  firstIssue: number;
  lastIssue: number;
  kind: string;
  kindLabel: string;
  title: string;
  summary: string | null;
  adm: string;
  orbit: string | null;
  band: string | null;
  sourceId: string;
  relatedSourceIds: string[];
  provision: string | null;
  sections: string[];
  coordProvisions: string[];
  severity: Severity;
  severityReasons: string[];
  confidence: Confidence | null;
  confidenceReasons: string[];
  marginDb: number | null;
  stationCount: number | null;
  stations: StationRef[];
  stationIds: string[];
  stationClasses: Record<string, number>;
  bands: BandMatch[];
  visibility: string[];
  basis: string | null;
  calc: PfdCalculation | null;
  result: CalculatedResult | null;
  terrestrial: TerrestrialDetail | null;
  windowDeadline: string | null;
  assumptions: string[];
  citations: string[];
  evidence: { table: string; key: string }[];
  file: SourceFile;
  updatedAt: string | null;
  review: ReviewState;
}

export interface ReviewEvent {
  at: string;
  action: string;
  note: string;
}

export interface ReviewState {
  status: ReviewStatus;
  note: string;
  letter: string | null;
  history: ReviewEvent[];
}

export interface Run {
  id: string;
  trigger: RunTrigger;
  domain: Domain;
  issue: number | null;
  fileName: string;
  sha256: string;
  sizeBytes: number | null;
  startedAt: string;
  status: RunStatus;
  stageIndex: number;
  issueId: string | null;
  message: string;
}

export interface DomainFreshness {
  domain: Domain;
  latestIssue: number | null;
  publishedOn: string | null;
  ageDays: number | null;
  stale: boolean;
}

export interface MonitorStatus {
  active: boolean;
  running: boolean;
  domains: DomainFreshness[];
  lastCheck: { at: string; label: string; status: string; errors: string[] } | null;
  schedule: string | null;
  nextCheckInS: number | null;
  nextExpectedOn: string | null;
  note: string | null;
}

/** One pass of the service that checks ITU for new issues. */
export interface SyncRun {
  id: number;
  trigger: string;
  startedAt: string;
  finishedAt: string | null;
  status: string;
  discovered: number;
  fetched: number;
  errors: string[];
}

export interface ReferenceSource {
  name: string;
  version: string;
  detail: string;
  url: string | null;
  status: 'loaded' | 'partial' | 'planned';
}

export interface LimitRow {
  citation: string;
  refBandwidth: string;
  rule: string;
}

export interface NewRun {
  file: File;
  sha256: string;
  domain: Domain;
  issue: number | null;
}

export interface Capabilities {
  upload: boolean; // can analyse a file sent from the browser
  checkItuNow: boolean;
  rescreen: boolean;
  reviewStatuses: ReviewStatus[];
  letterStorage: 'browser' | 'server';
}

/** Anything implementing this can drive the UI: sample data or the live services. */
export interface Api {
  mode: 'sample' | 'live';
  capabilities: Capabilities;
  listIssues(): Promise<IssueSummary[]>;
  getMonitor(): Promise<MonitorStatus>;
  listFindings(issueId: string): Promise<Finding[]>;
  getFinding(id: string): Promise<Finding | null>;
  updateReview(id: string, change: { status?: ReviewStatus; note?: string; letter?: string }): Promise<Finding>;
  listRuns(): Promise<Run[]>;
  getRun(id: string): Promise<Run | null>;
  listSyncRuns(): Promise<SyncRun[]>;
  findIssueByHash(sha256: string): Promise<IssueSummary | null>;
  createRun(input: NewRun, onProgress?: (fraction: number) => void): Promise<Run>;
  checkItuNow(): Promise<void>;
  rescreen(issue: IssueSummary): Promise<void>;
  listReferenceSources(): Promise<ReferenceSource[]>;
  listLimits(): Promise<LimitRow[]>;
  listSeverityRules(): Promise<{ severity: string; rule: string }[]>;
  severityNote: string;
}

export const LIVE_STAGES = ['File checked and stored', 'Screening', 'Results ready'] as const;

export const PIPELINE_STAGES = [
  'Upload and fingerprint file',
  'Read database tables',
  'Normalise to shared schema',
  'Filter for Finnish relevance',
  'Run calculations',
  'Rank findings',
  'Prepare drafts for review',
] as const;
