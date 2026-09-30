import { useEffect, useState } from 'react'
import {
  Activity,
  ArrowLeft,
  Clock3,
  FileUp,
  History,
  Play,
  ShieldCheck,
} from 'lucide-react'

import {
  AnalysisResult,
  AnalysisStatus,
  artifactUrl,
  createAnalysis,
  demoFiles,
  getHistory,
  getResult,
  getStatus,
} from './api'

type View = 'upload' | 'processing' | 'result' | 'history'

function formatValue(value: unknown, digits = 2): string {
  if (typeof value === 'number') return value.toFixed(digits)
  return value == null ? 'Unavailable' : String(value)
}

export default function App() {
  const [view, setView] = useState<View>('upload')
  const [walking, setWalking] = useState<File | null>(null)
  const [standing, setStanding] = useState<File | null>(null)
  const [job, setJob] = useState<AnalysisStatus | null>(null)
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [history, setHistory] = useState<AnalysisStatus[]>([])
  const [error, setError] = useState('')

  async function submit(walkingFile = walking, standingFile = standing) {
    if (!walkingFile) return
    setError('')
    try {
      const created = await createAnalysis(walkingFile, standingFile)
      setJob({
        ...created,
        created_at: new Date().toISOString(),
        started_at: null,
        completed_at: null,
        input_metadata: {},
        error_code: null,
        error_message: null,
      })
      setView('processing')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Request failed')
    }
  }

  async function loadDemo() {
    setError('')
    const [walkingFile, standingFile] = await demoFiles()
    setWalking(walkingFile)
    setStanding(standingFile)
    await submit(walkingFile, standingFile)
  }

  async function showHistory() {
    try {
      setHistory((await getHistory()).items)
      setView('history')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'History unavailable')
    }
  }

  async function openAnalysis(id: string) {
    const current = await getStatus(id)
    setJob(current)
    if (current.status === 'SUCCEEDED') {
      setResult(await getResult(id))
      setView('result')
    } else {
      setView('processing')
    }
  }

  const jobId = job?.analysis_id
  useEffect(() => {
    if (view !== 'processing' || !jobId) return
    const timer = window.setInterval(async () => {
      try {
        const current = await getStatus(jobId)
        setJob(current)
        if (current.status === 'SUCCEEDED') {
          setResult(await getResult(current.analysis_id))
          setView('result')
        } else if (current.status === 'FAILED') {
          setError(current.error_message ?? 'Analysis failed')
          setView('upload')
        }
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : 'Status check failed')
        setView('upload')
      }
    }, 800)
    return () => window.clearInterval(timer)
  }, [view, jobId])

  return (
    <>
      <header className="topbar">
        <button className="brand" onClick={() => setView('upload')}>
          <Activity /> <span>StepWise</span>
        </button>
        <nav>
          <button onClick={() => setView('upload')}><FileUp /> Analyze</button>
          <button onClick={showHistory}><History /> History</button>
        </nav>
      </header>

      <main>
        {error && <div className="error" role="alert">{error}</div>}

        {view === 'upload' && (
          <section className="workspace">
            <div className="intro">
              <p className="eyebrow">Wearable sensor analytics</p>
              <h1>Turn pressure and IMU recordings into an explainable screening report.</h1>
              <p>
                Upload a StepWise trial or run the deterministic synthetic demo. Results are
                engineering indicators, not medical diagnoses.
              </p>
            </div>
            <div className="upload-panel">
              <label>
                <span>Walking TXT</span>
                <input
                  type="file"
                  accept=".txt,text/plain"
                  onChange={(event) => setWalking(event.target.files?.[0] ?? null)}
                />
                <small>{walking?.name ?? 'Required'}</small>
              </label>
              <label>
                <span>Standing calibration TXT</span>
                <input
                  type="file"
                  accept=".txt,text/plain"
                  onChange={(event) => setStanding(event.target.files?.[0] ?? null)}
                />
                <small>{standing?.name ?? 'Optional, recommended for calibrated orientation'}</small>
              </label>
              <div className="actions">
                <button className="primary" disabled={!walking} onClick={() => submit()}>
                  <FileUp /> Analyze files
                </button>
                <button className="secondary" onClick={loadDemo}>
                  <Play /> Load demo data
                </button>
              </div>
              <p className="demo-note">
                <ShieldCheck /> Demo files are synthetic software-test data, not clinical
                validation data.
              </p>
            </div>
          </section>
        )}

        {view === 'processing' && (
          <section className="processing">
            <div className="spinner" />
            <p className="eyebrow">{job?.status}</p>
            <h1>Analysis in progress</h1>
            <p>
              The worker is segmenting stance phases, extracting features, evaluating conservative
              rules, and building report artifacts.
            </p>
            <code>{job?.analysis_id}</code>
          </section>
        )}

        {view === 'result' && result && (
          <>
            <section className="result-head">
              <button className="back" onClick={() => setView('upload')}>
                <ArrowLeft /> New analysis
              </button>
              <p className="eyebrow">Analysis complete</p>
              <h1>Screening result</h1>
              <div className="metrics">
                <div><span>Data quality</span><strong>{formatValue(result.result.summary.data_quality)}</strong></div>
                <div><span>Stances</span><strong>{formatValue(result.result.summary.detected_steps_single_foot, 0)}</strong></div>
                <div><span>Sample rate</span><strong>{formatValue(result.result.summary.estimated_sample_rate_hz)} Hz</strong></div>
                <div><span>Duration</span><strong>{formatValue(result.result.summary.duration_s)} s</strong></div>
              </div>
            </section>
            <section>
              <h2>Screening cards</h2>
              <div className="findings">
                {result.result.screening_cards.map((card) => (
                  <article key={card.title}>
                    <div className="finding-title">
                      <h3>{card.title}</h3>
                      <span className={`level ${card.level.toLowerCase()}`}>{card.level}</span>
                    </div>
                    <p>{card.interpretation}</p>
                    <dl>
                      <dt>Evidence</dt><dd>{card.evidence.join(' | ')}</dd>
                      <dt>Next step</dt><dd>{card.action}</dd>
                    </dl>
                    <small>{card.limitation}</small>
                  </article>
                ))}
              </div>
            </section>
            <section>
              <div className="section-head">
                <h2>Signal review</h2>
                <a href={artifactUrl(result.artifact_urls['report.html'])} target="_blank">
                  Open full report
                </a>
              </div>
              <div className="charts">
                <img src={artifactUrl(result.artifact_urls['pressure.png'])} alt="Pressure chart" />
                <img src={artifactUrl(result.artifact_urls['orientation.png'])} alt="Orientation chart" />
              </div>
            </section>
            <p className="disclaimer">
              StepWise is a non-diagnostic engineering screening prototype and is not clinically
              validated.
            </p>
          </>
        )}

        {view === 'history' && (
          <section>
            <button className="back" onClick={() => setView('upload') }>
              <ArrowLeft /> Back
            </button>
            <div className="section-head">
              <div><p className="eyebrow">Persisted jobs</p><h1>Analysis history</h1></div>
              <Clock3 />
            </div>
            <div className="history-list">
              {history.map((item) => (
                <button key={item.analysis_id} onClick={() => openAnalysis(item.analysis_id)}>
                  <span>
                    <strong>{item.status}</strong>
                    <small>{new Date(item.created_at).toLocaleString()}</small>
                  </span>
                  <code>{item.analysis_id.slice(0, 8)}</code>
                </button>
              ))}
            </div>
          </section>
        )}
      </main>
    </>
  )
}
