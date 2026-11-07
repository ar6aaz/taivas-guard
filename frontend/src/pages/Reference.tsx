import { api } from '../api';
import { Loading, useLoad } from '../components/ui';

const STATUS = { loaded: 'In use', partial: 'Partly in use', planned: 'Not used yet' } as const;

export function Reference() {
  const { data, error } = useLoad(async () => ({ sources: await api.listReferenceSources(), limits: await api.listLimits(), severity: await api.listSeverityRules() }), []);
  if (!data) return <Loading error={error} />;
  return (
    <>
      <h1>Rules and data</h1>
      <p className="lead">What the system compares filings against, and how it scores them. Everything here is visible so an expert can challenge it.</p>

      <section className="panel">
        <h2>Reference data</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Source</th><th>Version</th><th>Status</th><th>Notes</th></tr></thead>
            <tbody>{data.sources.map((s) => (
              <tr key={s.name}>
                <td>{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.name}</a> : s.name}</td>
                <td className="wrap">{s.version}</td>
                <td className="nowrap"><span className={`badge src-${s.status}`}>{STATUS[s.status]}</span></td>
                <td>{s.detail}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h2>Power flux density limits in use</h2>
        {data.limits.length === 0 ? <p className="muted">None yet. The pfd engine hasn’t been added to the screening service, so no case can confirm that a limit is exceeded.</p> : (
          <>
            <p className="muted">dB(W/m²), where δ is the arrival angle. Check these against the Radio Regulations PDF before relying on them.</p>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Limit</th><th>Reference bandwidth</th><th>Rule</th></tr></thead>
                <tbody>{data.limits.map((l) => <tr key={l.citation}><td className="nowrap">{l.citation}</td><td>{l.refBandwidth}</td><td>{l.rule}</td></tr>)}</tbody>
              </table>
            </div>
          </>
        )}
      </section>

      <section className="panel">
        <h2>How severity is scored</h2>
        <p className="muted">{api.severityNote}</p>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Severity</th><th>Rule</th></tr></thead>
            <tbody>{data.severity.map((r) => <tr key={r.severity}><td>{r.severity}</td><td>{r.rule}</td></tr>)}</tbody>
          </table>
        </div>
      </section>
    </>
  );
}
