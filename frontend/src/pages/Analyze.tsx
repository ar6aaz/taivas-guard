import { useId, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import type { Domain, IssueSummary } from '../api/types';
import { Hash } from '../components/ui';
import { checkFile, sha256Hex, type FileCheck } from '../lib/fileCheck';
import { formatBytes, formatDate } from '../lib/format';

type Step =
  | { name: 'idle'; error?: string }
  | { name: 'checking'; file: File }
  | { name: 'ready'; file: File; check: FileCheck; sha: string; known: IssueSummary | null };

export function Analyze() {
  const caps = api.capabilities;
  const [step, setStep] = useState<Step>({ name: 'idle' });
  const [domain, setDomain] = useState<Domain | ''>('');
  const [issue, setIssue] = useState('');
  const [dragging, setDragging] = useState(false);
  const [starting, setStarting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [screen, setScreen] = useState<{ text: string; bad: boolean } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const ids = { domain: useId(), issue: useId(), hint: useId() };

  async function take(file: File | undefined) {
    if (!file) return;
    setStep({ name: 'checking', file });
    try {
      const check = await checkFile(file);
      if (!check.ok) {
        setStep({ name: 'idle', error: `${file.name}: ${check.message}` });
        return;
      }
      if (!crypto.subtle) {
        setStep({ name: 'idle', error: 'This browser can’t fingerprint files on an insecure connection. Open the app over HTTPS or localhost.' });
        return;
      }
      const sha = await sha256Hex(file);
      setDomain(check.domain ?? '');
      setIssue(check.issue ? String(check.issue) : '');
      setScreen(null);
      setStep({ name: 'ready', file, check, sha, known: await api.findIssueByHash(sha) });
    } catch (e) {
      setStep({ name: 'idle', error: `Couldn’t read ${file.name}: ${(e as Error).message}` });
    }
  }

  function reset() {
    if (input.current) input.current.value = '';
    setScreen(null);
    setUploadError(null);
    setStep({ name: 'idle' });
  }

  async function rescreen(known: IssueSummary) {
    setScreen(null);
    try {
      await api.rescreen(known);
      setScreen({ text: `IFIC ${known.issue} is queued for screening. Results update on its findings page.`, bad: false });
    } catch (e) {
      setScreen({ text: (e as Error).message, bad: true });
    }
  }

  async function start() {
    if (step.name !== 'ready' || !domain) return;
    setStarting(true);
    setProgress(0);
    setUploadError(null);
    try {
      const run = await api.createRun({ file: step.file, sha256: step.sha, domain, issue: issue ? Number(issue) : null }, setProgress);
      navigate(`/runs/${run.id}`);
    } catch (e) {
      setUploadError((e as Error).message);
      setStarting(false);
    }
  }

  return (
    <div className="narrow">
      <h1>Analyse a file</h1>
      <p className="lead">Check an IFIC issue now rather than waiting for the scheduled run. This is useful for an older issue, a correction, or a file ITU sent you directly.</p>

      {step.name !== 'ready' && (
        <label
          className={`dropzone${dragging ? ' dropzone-on' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => { e.preventDefault(); setDragging(false); void take(e.dataTransfer.files[0]); }}
        >
          <input ref={input} className="visually-hidden" type="file" accept=".zip,.mdb,application/zip" aria-describedby={ids.hint} disabled={step.name === 'checking'} onChange={(e) => void take(e.target.files?.[0])} />
          {step.name === 'checking' ? (
            <span role="status"><strong>Checking {step.file.name}…</strong><span className="muted block">Large files can take a minute. Nothing has left your browser.</span></span>
          ) : (
            <span>
              <svg className="dropzone-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 16V5" /><path d="m7 10 5-5 5 5" /><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" /></svg>
              <strong>Drop a file here, or <span className="linklike">browse</span></strong><span className="muted block" id={ids.hint}>.zip or .mdb, up to 1 GB. Space and terrestrial IFIC files are both supported.</span></span>
          )}
        </label>
      )}

      {step.name === 'idle' && step.error && <p className="alert" role="alert">{step.error}</p>}

      {step.name === 'ready' && (
        <section className="panel">
          <div className="panel-head">
            <h2>{step.file.name}</h2>
            <button type="button" className="linkbtn" onClick={reset}>Choose another file</button>
          </div>
          <dl className="plain">
            <div><dt>Contents</dt><dd>{step.check.message}</dd></div>
            <div><dt>Size</dt><dd>{formatBytes(step.file.size)}</dd></div>
            <div><dt>SHA-256</dt><dd><Hash value={step.sha} /></dd></div>
          </dl>

          {step.known && (
            <div className="notice" role="status">
              <strong>We already have results for this exact file.</strong> It matches IFIC {step.known.issue} ({step.known.domain}){step.known.releasedOn ? `, released ${formatDate(step.known.releasedOn)}` : ''}.{' '}
              <Link to={`/findings?issue=${step.known.id}`}>View those results</Link>
            </div>
          )}

          {screen && <p className={screen.bad ? 'alert' : 'notice'} role="status">{screen.text}</p>}

          <div className="form-row">
            <div className="field">
              <label htmlFor={ids.domain}>Type</label>
              <select id={ids.domain} value={domain} onChange={(e) => setDomain(e.target.value as Domain | '')}>
                <option value="">Choose…</option>
                <option value="space">Space IFIC</option>
                <option value="terrestrial">Terrestrial IFIC</option>
              </select>
              {!domain && <p className="field-hint">We couldn’t tell from the file name.</p>}
            </div>
            <div className="field">
              <label htmlFor={ids.issue}>IFIC number</label>
              <input id={ids.issue} inputMode="numeric" value={issue} placeholder="e.g. 3083" onChange={(e) => setIssue(e.target.value.replace(/\D/g, '').slice(0, 5))} />
            </div>
          </div>

          {caps.upload && api.mode === 'sample' && (
            <p className="notice notice-warn"><strong>This run is a simulation.</strong> The pipeline isn’t connected, so the file stays in your browser and won’t produce results. The checks above are real.</p>
          )}

          {uploadError && <p className="alert" role="alert">{uploadError}</p>}
          {starting && api.mode === 'live' && (
            <div className="progress" role="status">
              <span className="muted">Uploading {Math.round(progress * 100)}%{progress >= 1 ? '. Checking the file on the server…' : ''}</span>
              <div className="meter"><div className="meter-fill" style={{ width: `${progress * 100}%` }} /></div>
            </div>
          )}

          <div className="actions">
            {caps.upload && (!step.known || !caps.rescreen) && <button type="button" className="btn btn-primary" disabled={!domain || starting} onClick={() => void start()}>{starting ? 'Uploading…' : step.known && !caps.rescreen ? 'Run it again anyway' : 'Start analysis'}</button>}
            {caps.rescreen && step.known && <button type="button" className="btn btn-primary" onClick={() => void rescreen(step.known!)}>Screen it again</button>}
          </div>
        </section>
      )}

      <section className="panel subtle">
        <h2>What happens to a file</h2>
        <ol className="steps-plain">
          <li>Your browser checks that it’s really a ZIP or Access file, and fingerprints it. Nothing is uploaded until you start the analysis.</li>
          <li>The server checks the file again and stores it, then screening compares it against Finnish frequency use.</li>
          <li>Cases appear for expert review, each tied to its source notices. No letter is ever sent automatically.</li>
        </ol>
      </section>
    </div>
  );
}
