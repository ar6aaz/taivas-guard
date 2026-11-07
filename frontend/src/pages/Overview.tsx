import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import type { Domain, Finding, IssueSummary, MonitorStatus } from '../api/types';
import { Funnel } from '../components/Funnel';
import { Hash, Loading, ScreenStatusBadge, SeverityBadge, useLoad } from '../components/ui';
import { dateOr, daysUntil, db, formatDateTime, formatDuration } from '../lib/format';

export function Overview() {
  const [params, setParams] = useSearchParams();
  const [tick, setTick] = useState(0);
  const { data, error } = useLoad(async () => ({ issues: await api.listIssues(), monitor: await api.getMonitor() }), [tick]);

  // Keep the page current while a sync or screening is in progress.
  const busy = !!data && (data.monitor.running || data.issues.some((i) => i.screenStatus === 'pending'));
  useEffect(() => {
    if (!busy) return undefined;
    const t = window.setTimeout(() => setTick((n) => n + 1), 5000);
    return () => window.clearTimeout(t);
  }, [busy, tick]);

  if (!data) return <Loading error={error} />;
  const { issues, monitor } = data;
  if (issues.length === 0) {
    return (
      <>
        <div className="empty panel"><h1>No IFIC issues yet</h1><p>The sync service hasn’t stored any issues. Check ITU now, or import a file.</p></div>
        <MonitorPanel monitor={monitor} onChange={() => setTick((n) => n + 1)} />
      </>
    );
  }
  // A file that failed screening shouldn't become the headline, or a bad upload would hide the real latest issue.
  const newestFirst = (d: Domain) => issues.filter((i) => i.domain === d).sort((a, b) => b.issue - a.issue);
  const latest = (d: Domain) => newestFirst(d).find((i) => i.screenStatus !== 'failed') ?? newestFirst(d)[0];
  const domain = (params.get('view') === 'terrestrial' ? 'terrestrial' : 'space') as Domain;
  const issue = latest(domain) ?? issues[0];
  const evaluation = issues.filter((i) => i.evaluationIssue);

  return (
    <>
      <div className="overview-head">
        <div>
          <p className="eyebrow">Latest {issue.domain} release</p>
          <div className="hero-title"><h1>BR IFIC {issue.issue}</h1><ScreenStatusBadge value={issue.screenStatus} /></div>
        </div>
        <div className="tabs" role="tablist" aria-label="Choose results">
          {(['space', 'terrestrial'] as Domain[]).map((d) => (
            <button key={d} role="tab" type="button" aria-selected={domain === d} className={domain === d ? 'tab tab-on' : 'tab'} onClick={() => setParams(d === 'space' ? {} : { view: d })}>
              {d === 'space' ? 'Space' : 'Terrestrial'}
            </button>
          ))}
        </div>
      </div>

      <section className="hero">
        <div className="hero-main">
          <Countdown issue={issue} />
          <dl className="hero-facts">
            <div><dt>Released</dt><dd>{dateOr(issue.releasedOn)}</dd></div>
            <div><dt>Source file</dt><dd>{issue.file.zip} <Hash value={issue.file.zipSha256} /></dd></div>
            {(issue.ruleCounts.length ? issue.counts.slice(0, 1) : issue.counts).map((c) => <div key={c.label}><dt>{c.label}</dt><dd className="tnum">{c.value.toLocaleString('en-GB')}</dd></div>)}
          </dl>
        </div>
        <MonitorPanel monitor={monitor} onChange={() => setTick((n) => n + 1)} />
      </section>

      <IssueResults issue={issue} tick={tick} />

      {evaluation.length > 0 && (
        <section className="panel">
          <h2>Issues named in the Traficom brief</h2>
          <p className="muted">These are the issues the challenge evaluates.</p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Issue</th><th>Type</th><th>Screening</th><th className="num">{evaluation[0].counts[0].label}</th><th aria-label="Link" /></tr></thead>
              <tbody>
                {evaluation.map((i) => (
                  <tr key={i.id}>
                    <td className="nowrap">IFIC {i.issue}</td><td>{i.domain === 'space' ? 'Space' : 'Terrestrial'}</td><td><ScreenStatusBadge value={i.screenStatus} /></td><td className="num">{i.counts[0].value.toLocaleString('en-GB')}</td>
                    <td className="right nowrap"><Link to={`/findings?issue=${i.id}`}>View findings</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}

function Countdown({ issue }: { issue: IssueSummary }) {
  const deadline = issue.commentDeadline;
  if (!deadline) {
    return (
      <div className="countdown">
        <p className="countdown-label">Comment window</p>
        <p className="countdown-none">No window identified</p>
      </div>
    );
  }
  const left = daysUntil(deadline);
  const closed = left < 0;
  const total = issue.releasedOn ? daysUntil(deadline, new Date(`${issue.releasedOn}T12:00:00Z`)) : null;
  const used = total ? Math.min(Math.max((total - left) / total, 0), 1) : null;
  return (
    <div className="countdown">
      <p className="countdown-label">Comment window</p>
      <p className={left >= 0 && left <= 30 ? 'countdown-value urgent' : 'countdown-value'}>
        {closed ? 'Closed' : <>{left}<span> days left</span></>}
      </p>
      {used !== null && !closed && (
        <div className="meter" role="img" aria-label={`${Math.round(used * 100)}% of the comment window has passed`}><div className="meter-fill" style={{ width: `${used * 100}%` }} /></div>
      )}
      <p className="fine">{closed ? 'Closed on' : 'Closes'} {dateOr(deadline)}. An estimate: publication date plus four months, so confirm it against each Special Section.</p>
    </div>
  );
}

function MonitorPanel({ monitor, onChange }: { monitor: MonitorStatus; onChange: () => void }) {
  const [message, setMessage] = useState<{ text: string; bad: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const stale = monitor.domains.filter((d) => d.stale);
  const failed = monitor.lastCheck && monitor.lastCheck.status !== 'ok' && monitor.lastCheck.status !== 'running';

  async function check() {
    setBusy(true);
    setMessage(null);
    try {
      await api.checkItuNow();
      setMessage({ text: 'Checking ITU for new issues.', bad: false });
      onChange();
    } catch (e) {
      setMessage({ text: (e as Error).message, bad: true });
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="monitor">
      <h2>ITU monitor</h2>
      <p className="monitor-state"><span className={`dot ${monitor.active ? 'dot-on' : 'dot-off'}`} aria-hidden="true" /> {monitor.running ? 'Checking ITU now' : monitor.active ? 'Running' : 'Not running'}</p>
      <dl className="plain">
        {monitor.domains.map((d) => (
          <div key={d.domain}><dt>Latest {d.domain}</dt><dd>{d.latestIssue ? `${d.latestIssue}${d.publishedOn ? `, ${dateOr(d.publishedOn)}` : ''}` : 'None yet'}</dd></div>
        ))}
        {monitor.lastCheck && <div><dt>Last check</dt><dd>{formatDateTime(monitor.lastCheck.at)}<span className="muted block">{monitor.lastCheck.label}, {monitor.lastCheck.status}</span></dd></div>}
        {monitor.nextCheckInS !== null && <div><dt>Next check</dt><dd>In {formatDuration(monitor.nextCheckInS)}{monitor.schedule && <span className="muted block">Schedule: <code>{monitor.schedule}</code></span>}</dd></div>}
        {monitor.nextExpectedOn && <div><dt>Next release expected</dt><dd>{dateOr(monitor.nextExpectedOn)}</dd></div>}
      </dl>
      {stale.length > 0 && <p className="notice notice-warn"><strong>Overdue:</strong> no recent {stale.map((d) => d.domain).join(' or ')} issue. The sync may be broken, or the date may not have been recorded on import.</p>}
      {failed && <p className="alert">The last check ended as “{monitor.lastCheck!.status}”.{monitor.lastCheck!.errors[0] ? ` ${monitor.lastCheck!.errors[0]}` : ''}</p>}
      {monitor.note && <p className="fine">{monitor.note}</p>}
      {message && <p className={message.bad ? 'alert' : 'notice'} role="status">{message.text}</p>}
      {api.capabilities.checkItuNow && <button type="button" className="btn btn-secondary" disabled={busy || monitor.running} onClick={() => void check()}>Check ITU now</button>}
    </aside>
  );
}

function IssueResults({ issue, tick }: { issue: IssueSummary; tick: number }) {
  const { data: findings, error } = useLoad(() => api.listFindings(issue.id), [issue.id, tick]);
  const sev = issue.severityCounts;
  const hasFunnel = issue.funnel.length > 0;
  const nearBorder = findings?.some((f) => f.terrestrial) ?? false;
  const pending = findings?.filter((f) => f.review.status === 'pending').length;
  return (
    <div role="tabpanel">
      <ScreenNotice issue={issue} />
      {issue.screenStatus !== 'no_rules' && (
        <section className="stats" aria-label="Results summary">
          {nearBorder ? (
            <>
              <Stat value={issue.funnel[3]?.n ?? 0} label="Foreign land-mobile stations within 100 km" to={`/findings?issue=${issue.id}`} />
              <Stat value={issue.funnel[2]?.n ?? 0} label="Within 300 km" to={`/findings?issue=${issue.id}`} />
              <Stat value={sev.unscored} label="Listed here, waiting for a calculation" to={`/findings?issue=${issue.id}`} />
            </>
          ) : (
            <>
              <Stat value={sev.high} label="High severity" tone="high" to={`/findings?issue=${issue.id}&severity=high`} />
              <Stat value={sev.medium} label="Medium severity" tone="medium" to={`/findings?issue=${issue.id}&severity=medium`} />
              <Stat value={issue.brListedNetworks} label="Networks where ITU names Finland" to={`/findings?issue=${issue.id}&kind=${hasFunnel ? 'br_listed' : 'fin-named-by-br'}`} />
            </>
          )}
          <Stat value={pending ?? '–'} label="Waiting for expert review" to={`/findings?issue=${issue.id}&review=pending`} />
        </section>
      )}

      {issue.screenStatus !== 'no_rules' && (
        <div className="split">
          <section className="panel">
            <h2>{hasFunnel ? 'How the issue narrowed down' : 'Cases by rule'}</h2>
            {hasFunnel ? <Funnel stages={issue.funnel} /> : issue.ruleCounts.length ? <Funnel stages={issue.ruleCounts} ordered={false} /> : <p className="muted">No cases in this issue.</p>}
          </section>
          <section className="panel">
            <div className="panel-head">
              <h2>Top cases</h2>
              <Link to={`/findings?issue=${issue.id}`}>All findings</Link>
            </div>
            {!findings ? <Loading error={error} /> : findings.length ? <TopFindings rows={findings.slice(0, 6)} /> : <p className="muted">No cases in this issue.</p>}
          </section>
        </div>
      )}

      <section className="panel callout">
        <h2>What this run didn’t check</h2>
        <ul>{issue.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
      </section>
    </div>
  );
}

function ScreenNotice({ issue }: { issue: IssueSummary }) {
  if (issue.screenStatus === 'pending') return <p className="notice" role="status"><strong>Screening is in progress.</strong> This page updates by itself, and large issues can take a few minutes.</p>;
  if (issue.screenStatus === 'failed') return <p className="alert" role="alert"><strong>Screening failed for this issue,</strong> so these results are missing or incomplete. Check the screening service’s log, then screen it again from Analyse a file.</p>;
  if (issue.screenStatus === 'no_rules') return <p className="notice notice-warn"><strong>No rules ran on this issue.</strong> The file is stored, but terrestrial screening doesn’t exist yet. This does not mean there are no concerns.</p>;
  return null;
}

function Stat({ value, label, to, tone }: { value: number | string; label: string; to: string; tone?: 'high' | 'medium' }) {
  return (
    <Link className="stat" to={to}>
      <span className="stat-value">{value}</span>
      <span className="stat-label">{tone && <span className={`stat-mark sev-text-${tone}`} aria-hidden="true">{tone === 'high' ? '▲' : '◆'}</span>}{label}</span>
    </Link>
  );
}

function TopFindings({ rows }: { rows: Finding[] }) {
  return (
    <ul className="top-list">
      {rows.map((f) => (
        <li key={f.id}>
          <Link to={`/findings/${f.id}`}>
            <span className="top-main"><strong>{f.title}</strong> <span className="muted">{f.adm}{f.band ? ` · ${f.band}` : ` · ${f.kindLabel}`}</span></span>
            <span className="top-meta">
              {f.marginDb !== null && <span className="tnum">{db(f.marginDb)} dB</span>}
              {f.terrestrial && <span className="tnum">{f.terrestrial.borderKm} km</span>}
              <SeverityBadge value={f.severity} />
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
