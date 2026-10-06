import type { Activity, Artifact, Project, ProjectInput, ProviderCheck, QueueState, Review, Run, Settings, Ticket, TicketInput, TicketPatch } from './contracts';

const base = process.env.NODE_ENV === 'development' ? 'http://127.0.0.1:8765' : '';

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function request<T>(path: string, method = 'GET', body?: unknown, timeout = 15000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(`${base}/api${path}`, {
      method, signal: controller.signal, cache: 'no-store',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new ApiError(response.status, typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail ?? data));
    }
    return data as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof Error && error.name === 'AbortError') throw new Error('The local service took too long to respond. Refresh to inspect persisted state.');
    throw new Error('Cannot reach the local service. Start the Python API and retry.');
  } finally { clearTimeout(timer); }
}

export const api = {
  health: () => request<{ service: string; version: string; mode: string }>('/health'),
  projects: () => request<Project[]>('/projects'),
  createProject: (input: ProjectInput) => request<Project>('/projects', 'POST', input),
  updateProject: (id: string, input: ProjectInput) => request<Project>(`/projects/${id}`, 'PUT', input),
  tickets: (project?: string) => request<Ticket[]>(`/tickets${project ? `?project_id=${encodeURIComponent(project)}` : ''}`),
  createTicket: (input: TicketInput) => request<Ticket>('/tickets', 'POST', input),
  patchTicket: (id: string, input: TicketPatch) => request<Ticket>(`/tickets/${id}`, 'PATCH', input),
  queue: () => request<QueueState>('/queue'),
  start: () => request<QueueState>('/queue/start', 'POST'),
  pause: () => request<QueueState>('/queue/pause', 'POST'),
  runs: (ticket: string) => request<Run[]>(`/runs?ticket_id=${encodeURIComponent(ticket)}`),
  run: (id: string) => request<Run>(`/runs/${id}`),
  activity: (id: string, after = 0) => request<Activity[]>(`/runs/${id}/activity?after=${after}`),
  reviews: (id: string) => request<Review[]>(`/runs/${id}/reviews`),
  artifact: (id: string) => request<Artifact>(`/runs/${id}/artifact`),
  resume: (id: string) => request<Run>(`/runs/${id}/resume`, 'POST'),
  decide: (id: string, action: 'approve' | 'changes' | 'confirm-merge', artifact_commit: string, message: string) => request<Run>(`/runs/${id}/${action}`, 'POST', { artifact_commit, message }),
  settings: () => request<Settings>('/settings'),
  saveSettings: (settings: Settings) => request<Settings>('/settings', 'PUT', settings),
  checkProvider: (role: 'engineer' | 'frontier') => request<ProviderCheck>(`/providers/${role}/check`, 'POST', undefined, 125000),
};

declare global {
  interface Window { kanbanDesktop?: { selectRepository(): Promise<string | null> }; }
}
