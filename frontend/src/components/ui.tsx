import { useEffect, useState } from 'react';
import type { Confidence, ReviewStatus, RunStatus, RunTrigger, ScreenStatus, Severity } from '../api/types';
import { daysUntil, formatDate, shortHash } from '../lib/format';

const SEV: Record<Severity, { icon: string; label: string }> = {
  high: { icon: '▲', label: 'High' },
  medium: { icon: '◆', label: 'Medium' },
  low: { icon: '●', label: 'Low' },
  unscored: { icon: '○', label: 'Unscored' },
};

export function SeverityBadge({ value }: { value: Severity }) {
  return <span className={`badge sev-${value}`}><span aria-hidden="true">{SEV[value].icon}</span> {SEV[value].label}</span>;
}

export function ConfidenceText({ value }: { value: Confidence }) {
  return <span>{{ medium: 'Medium', low: 'Low', not_calculated: 'Not calculated' }[value]}</span>;
}

const REVIEW: Record<ReviewStatus, string> = { pending: 'To review', accepted: 'Accepted', rejected: 'Rejected', needs_info: 'Needs info' };
export const reviewLabel = (s: ReviewStatus) => REVIEW[s];
export function ReviewBadge({ value }: { value: ReviewStatus }) {
  return <span className={`badge review-${value}`}>{REVIEW[value]}</span>;
}

const RUN: Record<RunStatus, string> = { running: 'Running', succeeded: 'Complete', failed: 'Failed', simulated: 'Simulated only' };
export function RunStatusBadge({ value }: { value: RunStatus }) {
  return <span className={`badge run-${value}`}>{RUN[value]}</span>;
}
const SCREEN: Record<ScreenStatus, string> = { pending: 'Screening', done: 'Screened', no_rules: 'No rules yet', failed: 'Screening failed' };
export function ScreenStatusBadge({ value }: { value: ScreenStatus }) {
  return <span className={`badge screen-${value}`}>{SCREEN[value] ?? value}</span>;
}
const SYNC: Record<string, string> = { running: 'Running', ok: 'Complete', partial: 'Partial', failed: 'Failed' };
export function SyncStatusBadge({ value }: { value: string }) {
  return <span className={`badge sync-${value}`}>{SYNC[value] ?? value}</span>;
}
export const triggerLabel = (t: RunTrigger) => ({ scheduled: 'Scheduled', on_demand: 'On demand', sample: 'Sample' })[t];

export function CommentWindow({ deadline }: { deadline: string | null }) {
  if (!deadline) return <span className="muted">No window</span>;
  const d = daysUntil(deadline);
  if (d < 0) return <span className="muted">Closed {formatDate(deadline)}</span>;
  return <span><strong className={d <= 30 ? 'urgent' : undefined}>{d} days left</strong> <span className="muted">· {formatDate(deadline)}</span></span>;
}

export function Hash({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="hash"
      title={value}
      aria-label={`Copy SHA-256 ${value}`}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch { /* clipboard blocked: the full value is still in the tooltip */ }
      }}
    >
      {copied ? 'Copied' : shortHash(value)}
    </button>
  );
}

/** Loads async data with explicit loading and error states. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [state, setState] = useState<{ data?: T; error?: string; loading: boolean }>({ loading: true });
  useEffect(() => {
    let live = true;
    setState((s) => ({ ...s, loading: true, error: undefined }));
    load().then(
      (data) => live && setState({ data, loading: false }),
      (e: Error) => live && setState({ error: e.message, loading: false }),
    );
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

export function Loading({ error }: { error?: string }) {
  if (error) return <div className="empty" role="alert"><h2>Couldn’t load this</h2><p>{error}</p></div>;
  return <p className="muted" role="status">Loading…</p>;
}
