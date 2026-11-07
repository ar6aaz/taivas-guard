import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api';
import { LIVE_STAGES, PIPELINE_STAGES, type Run } from '../api/types';
import { Hash, Loading, RunStatusBadge, triggerLabel } from '../components/ui';
import { formatBytes, formatDateTime } from '../lib/format';

export function RunDetail() {
  const { id = '' } = useParams();
  const [run, setRun] = useState<Run | null | undefined>(undefined);

  useEffect(() => {
    let live = true;
    let timer: number;
    const tick = async () => {
      const r = await api.getRun(id);
      if (!live) return;
      setRun(r);
      if (r?.status === 'running') timer = window.setTimeout(tick, api.mode === 'live' ? 2000 : 500);
    };
    void tick();
    return () => { live = false; clearTimeout(timer); };
  }, [id]);

  if (run === undefined) return <Loading />;
  if (run === null) return <div className="empty"><h1>Run not found</h1><Link to="/runs">Back to runs</Link></div>;
  const done = run.status !== 'running';
  const live = api.mode === 'live';
  const stages: readonly string[] = live ? LIVE_STAGES : PIPELINE_STAGES;

  return (
    <div className="narrow">
      <p className="crumb"><Link to="/runs">Runs</Link> / {run.fileName}</p>
      <div className="page-head">
        <h1>{run.issue ? `IFIC ${run.issue}` : run.fileName}</h1>
        <RunStatusBadge value={run.status} />
      </div>
      <dl className="plain panel">
        <div><dt>Trigger</dt><dd>{triggerLabel(run.trigger)}</dd></div>
        <div><dt>Type</dt><dd>{run.domain === 'space' ? 'Space IFIC' : 'Terrestrial IFIC'}</dd></div>
        <div><dt>Started</dt><dd>{formatDateTime(run.startedAt)}</dd></div>
        {run.sizeBytes !== null && <div><dt>Size</dt><dd>{formatBytes(run.sizeBytes)}</dd></div>}
        <div><dt>SHA-256</dt><dd><Hash value={run.sha256} /></dd></div>
      </dl>

      <section className="panel">
        <h2>Progress{!live && run.trigger === 'on_demand' && run.status !== 'succeeded' ? ' (simulated)' : ''}</h2>
        <ol className="stages" aria-live="polite">
          {stages.map((s, i) => {
            const state = i < run.stageIndex ? 'done' : i === run.stageIndex && !done ? 'now' : 'wait';
            return (
              <li key={s} className={`stage stage-${state}`}>
                <span className="stage-mark" aria-hidden="true">{state === 'done' ? '✓' : ''}</span>
                <span>{s}{state === 'now' && <span className="visually-hidden"> (in progress)</span>}</span>
              </li>
            );
          })}
        </ol>
      </section>

      {done && (
        <p className={run.status === 'failed' ? 'alert' : run.status === 'simulated' ? 'notice notice-warn' : 'notice'} role="status">
          {run.message}
          {run.issueId && <> <Link to={`/findings?issue=${run.issueId}`}>View findings</Link></>}
        </p>
      )}
    </div>
  );
}
