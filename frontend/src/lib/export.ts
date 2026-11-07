import type { Finding } from '../api/types';

function saveAs(name: string, type: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

const esc = (v: unknown) => {
  const s = v === null || v === undefined ? '' : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

export function findingsCsv(rows: Finding[]): string {
  const head = ['finding_id', 'issue', 'domain', 'kind', 'network_or_site', 'administration', 'band', 'source_notice_id', 'sections', 'severity', 'confidence', 'margin_db', 'pfd_db', 'limit_db', 'angle_deg', 'finnish_stations', 'comment_deadline', 'review_status', 'citation', 'source_sha256'];
  const body = rows.map((f) =>
    [f.id, f.issue, f.domain, f.kind, f.title, f.adm, f.band, f.sourceId, f.sections.join(' '), f.severity, f.confidence, f.marginDb, f.calc?.pfd ?? f.result?.valueDb, f.calc?.limit ?? f.result?.limitDb, f.calc?.angleDeg, f.stationCount, f.windowDeadline, f.review.status, f.citations[0], f.file.zipSha256].map(esc).join(','),
  );
  return [head.join(','), ...body].join('\n');
}

export function exportFindings(issueId: string, rows: Finding[], format: 'csv' | 'json') {
  if (format === 'csv') saveAs(`${issueId}-findings.csv`, 'text/csv', findingsCsv(rows));
  else saveAs(`${issueId}-findings.json`, 'application/json', JSON.stringify(rows, null, 2));
}

export function exportText(name: string, text: string) {
  saveAs(name, 'text/plain', text);
}
