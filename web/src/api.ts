export type JobStatus = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'

export interface AnalysisStatus {
  analysis_id: string
  status: JobStatus
  created_at: string
  started_at: string | null
  completed_at: string | null
  input_metadata: Record<string, unknown>
  error_code: string | null
  error_message: string | null
}

export interface ScreeningCard {
  title: string
  level: string
  evidence: string[]
  interpretation: string
  action: string
  limitation: string
}

export interface AnalysisResult {
  analysis_id: string
  status: JobStatus
  result: {
    summary: Record<string, unknown>
    metrics: Record<string, number | null>
    screening_cards: ScreeningCard[]
  }
  artifact_urls: Record<string, string>
}

const API = import.meta.env.VITE_API_URL ?? ''

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, options)
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: 'Request failed' }))
    throw new Error(body.detail ?? body.message ?? 'Request failed')
  }
  return response.json() as Promise<T>
}

export async function createAnalysis(
  walking: File,
  standing: File | null,
): Promise<{ analysis_id: string; status: JobStatus }> {
  const form = new FormData()
  form.append('walking_file', walking)
  if (standing) form.append('standing_file', standing)
  form.append(
    'config_json',
    JSON.stringify({
      sensor_mapping: {
        heel: ['P2'],
        arch: ['P3'],
        medial_forefoot: ['P4'],
        lateral_forefoot: ['P1'],
        toe: [],
        pitch_eversion_sign: 'positive',
      },
      smooth_window: 3,
    }),
  )
  return request('/api/v1/analyses', { method: 'POST', body: form })
}

export function getStatus(id: string): Promise<AnalysisStatus> {
  return request(`/api/v1/analyses/${id}`)
}

export function getResult(id: string): Promise<AnalysisResult> {
  return request(`/api/v1/analyses/${id}/result`)
}

export function getHistory(): Promise<{ items: AnalysisStatus[]; total: number }> {
  return request('/api/v1/analyses?page=1&page_size=20')
}

export function artifactUrl(path: string): string {
  return `${API}${path}`
}

export async function demoFiles(): Promise<[File, File]> {
  const [walking, standing] = await Promise.all([
    fetch('/demo/normal_like.synthetic.txt').then((response) => response.blob()),
    fetch('/demo/standing_neutral.synthetic.txt').then((response) => response.blob()),
  ])
  return [
    new File([walking], 'normal_like.synthetic.txt', { type: 'text/plain' }),
    new File([standing], 'standing_neutral.synthetic.txt', { type: 'text/plain' }),
  ]
}
