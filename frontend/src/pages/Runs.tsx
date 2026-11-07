import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { Hash, Loading, RunStatusBadge, ScreenStatusBadge, SyncStatusBadge, triggerLabel, useLoad } from '../components/ui';
import { dateOr, formatDateTime } from '../lib/format';

export function Runs() {
  const [tick, setTick] = useState(0);
  const { data, error } = useLoad(async () => ({ runs: await api.listRuns(), sync: await api.listSyncRuns(), issues: await api.listIssues() }), [tick]);
  if (!data) return <Loading error={error} />;
  const { runs, sync, issues } = data;
  const live = api.mode === 'live';
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Runs</h1>
          <p className="lead">{live ? 'Each check for new ITU releases, and how screening went for each stored issue.' : 'Every time a file has gone through the system.'}</p>
        </div>
        <div className="actions">
          {live && <button type="button" className="btn btn-secondary" onClick={() => setTick((n) => n + 1)}>Refresh</button>}
          <Link className="btn btn-primary" to="/analyze">Analyse a file</Link>
        </div>
      </div>

      {live && (
        <>
          <section className="panel flush">
            <h2 className="flush-title">Issues and screening</h2>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Issue</th><th>Type</th><th>Released</th><th>Screening</th><th>Screened at</th><th className="num">Cases</th><th>SHA-256</th><th aria-label="Link" /></tr></thead>
                <tbody>
                  {issues.map((i) => (
                    <tr key={i.id}>
                      <td className="tnum">{i.issue}</td><td>{i.domain === 'space' ? 'Space' : 'Terrestrial'}</td><td className="nowrap">{dateOr(i.releasedOn)}</td><td><ScreenStatusBadge value={i.screenStatus} /></td>
                      <td className="nowrap">{i.screenedAt ? formatDateTime(`${i.screenedAt}Z`) : '–'}</td><td className="num">{i.counts[0].value}</td><td><Hash value={i.file.zipSha256} /></td>
                      <td className="right nowrap"><Link to={`/findings?issue=${i.id}`}>Findings</Link></td>
                    </tr>
                  ))}
                  {issues.length === 0 && <tr><td colSpan={8} className="muted">No issues stored yet.</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
          <section className="panel flush">
            <h2 className="flush-title">Checks for new ITU releases</h2>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Started</th><th>Trigger</th><th>Status</th><th className="num">Found</th><th className="num">Downloaded</th><th>Errors</th></tr></thead>
                <tbody>
                  {sync.map((r) => (
                    <tr key={r.id}>
                      <td className="nowrap">{formatDateTime(r.startedAt)}</td><td>{r.trigger}</td><td><SyncStatusBadge value={r.status} /></td><td className="num">{r.discovered}</td><td className="num">{r.fetched}</td>
                      <td className="wrap">{r.errors.length ? r.errors.join('; ') : <span className="muted">None</span>}</td>
                    </tr>
                  ))}
                  {sync.length === 0 && <tr><td colSpan={6} className="muted">No checks have run yet.</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      {!live && (
        <>
          <p className="notice">Sample mode has no scheduled runs. Connect the services to see each new release appear here automatically.</p>
          <div className="table-wrap panel flush">
            <table>
              <thead><tr><th>Started</th><th>Trigger</th><th>File</th><th>Issue</th><th>Status</th><th>SHA-256</th><th aria-label="Link" /></tr></thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id}>
                    <td className="nowrap">{formatDateTime(r.startedAt)}</td><td>{triggerLabel(r.trigger)}</td><td>{r.fileName}<span className="muted block">{r.domain === 'space' ? 'Space' : 'Terrestrial'}</span></td><td className="tnum">{r.issue ?? '–'}</td>
                    <td><RunStatusBadge value={r.status} /></td><td><Hash value={r.sha256} /></td><td className="right nowrap">{r.issueId ? <Link to={`/findings?issue=${r.issueId}`}>Findings</Link> : <Link to={`/runs/${r.id}`}>Details</Link>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
