/**
 * The API client.
 *
 * Two behaviours worth knowing about:
 *
 * 1. **Transparent refresh.** A 401 triggers one refresh attempt and one retry. Concurrent
 *    401s share a single in-flight refresh, so a page that fires six requests at mount does
 *    not burn six refresh tokens and log the user out.
 * 2. **Typed errors.** Every failure raises `ApiError` carrying the server's problem
 *    document, so a form can show a field-level message instead of "something went wrong".
 */

import { desktopToken } from '@/desktop';
import type {
  AnnotationDocument,
  AnnotationWriteResult,
  DatasetFormat,
  FrameInfo,
  InferenceResult,
  Issue,
  Job,
  Label,
  LocalImportResult,
  ModelRegistration,
  Organization,
  Page,
  ProblemDocument,
  Project,
  ProjectStatistics,
  Task,
  TaskMediaMeta,
  TaskProgress,
  TokenPair,
  User,
} from './types';

const API_PREFIX = '/api/v1';
const ACCESS_KEY = 'curvevision.access';
const REFRESH_KEY = 'curvevision.refresh';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: ProblemDocument,
  ) {
    super(problem.detail || problem.title || `Request failed (${status})`);
    this.name = 'ApiError';
  }

  /** Field-level messages, keyed by the dotted field path. */
  get fieldErrors(): Record<string, string> {
    const result: Record<string, string> = {};
    for (const error of this.problem.errors ?? []) {
      // The first element is always "body"/"query"; the useful part is what follows.
      const path = error.location.slice(1).join('.');
      result[path || '_'] = error.message;
    }
    return result;
  }
}

export const tokenStore = {
  get access(): string | null {
    return safeRead(ACCESS_KEY);
  },
  get refresh(): string | null {
    return safeRead(REFRESH_KEY);
  },
  set(pair: TokenPair): void {
    safeWrite(ACCESS_KEY, pair.access_token);
    safeWrite(REFRESH_KEY, pair.refresh_token);
  },
  clear(): void {
    safeRemove(ACCESS_KEY);
    safeRemove(REFRESH_KEY);
  },
};

// localStorage throws in private-mode Safari and when site data is blocked; the app should
// degrade to a session that ends on reload rather than failing to start.
function safeRead(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function safeWrite(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}
function safeRemove(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

let refreshInFlight: Promise<boolean> | null = null;

async function refreshTokens(): Promise<boolean> {
  const refresh = tokenStore.refresh;
  if (!refresh) return false;

  // Share one refresh across concurrent 401s; rotation means a second attempt with the
  // same token would fail and log the user out mid-session.
  refreshInFlight ??= (async () => {
    try {
      const response = await fetch(`${API_PREFIX}/auth/refresh`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!response.ok) {
        tokenStore.clear();
        return false;
      }
      tokenStore.set((await response.json()) as TokenPair);
      return true;
    } catch {
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  params?: Record<string, string | number | boolean | undefined | null>;
  formData?: FormData;
  signal?: AbortSignal;
  /** Internal: prevents a refresh loop. */
  retry?: boolean;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const url = new URL(`${API_PREFIX}${path}`, window.location.origin);
  for (const [key, value] of Object.entries(options.params ?? {})) {
    if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
  }

  const headers: Record<string, string> = {};
  // The desktop shell's token wins when there is one: it is the credential for this
  // launch, and there is no stored session to prefer over it. In a browser this is null
  // and nothing about the existing path changes.
  const access = desktopToken() ?? tokenStore.access;
  if (access) headers.authorization = `Bearer ${access}`;
  if (options.body !== undefined) headers['content-type'] = 'application/json';

  const response = await fetch(url.toString(), {
    method: options.method ?? 'GET',
    headers,
    body: options.formData ?? (options.body !== undefined ? JSON.stringify(options.body) : undefined),
    signal: options.signal,
  });

  if (response.status === 401 && !options.retry && tokenStore.refresh) {
    if (await refreshTokens()) {
      return request<T>(path, { ...options, retry: true });
    }
  }

  if (!response.ok) {
    let problem: ProblemDocument;
    try {
      problem = (await response.json()) as ProblemDocument;
    } catch {
      problem = {
        type: 'about:blank',
        title: response.statusText,
        status: response.status,
        detail: response.statusText,
      };
    }
    throw new ApiError(response.status, problem);
  }

  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('application/json')) return (await response.json()) as T;
  return (await response.blob()) as unknown as T;
}

export const api = {
  // ------------------------------------------------------------------------- auth
  async login(identifier: string, password: string): Promise<User> {
    const tokens = await request<TokenPair>('/auth/login', {
      method: 'POST',
      body: { identifier, password },
    });
    tokenStore.set(tokens);
    return request<User>('/auth/me');
  },

  async register(input: {
    email: string;
    username: string;
    password: string;
    full_name?: string;
  }): Promise<User> {
    await request<User>('/auth/register', { method: 'POST', body: input });
    return api.login(input.username, input.password);
  },

  async logout(): Promise<void> {
    const refresh = tokenStore.refresh;
    if (refresh) {
      await request<void>('/auth/logout', {
        method: 'POST',
        body: { refresh_token: refresh },
      }).catch(() => undefined);
    }
    tokenStore.clear();
  },

  me: () => request<User>('/auth/me'),

  // ---------------------------------------------------------------- organizations
  organizations: () => request<Organization[]>('/organizations'),

  createOrganization: (input: { slug: string; name: string; description?: string }) =>
    request<Organization>('/organizations', { method: 'POST', body: input }),

  // --------------------------------------------------------------------- projects
  projects: (params?: { organization_id?: string; search?: string; limit?: number }) =>
    request<Page<Project>>('/projects', { params }),

  project: (id: string) => request<Project>(`/projects/${id}`),

  createProject: (input: {
    organization_id: string;
    slug: string;
    name: string;
    description?: string;
    labels?: { name: string; color?: string }[];
  }) => request<Project>('/projects', { method: 'POST', body: input }),

  projectStatistics: (id: string) => request<ProjectStatistics>(`/projects/${id}/statistics`),

  labels: (projectId: string) => request<Label[]>(`/projects/${projectId}/labels`),

  createLabel: (projectId: string, input: { name: string; color?: string }) =>
    request<Label>(`/projects/${projectId}/labels`, { method: 'POST', body: input }),

  // ------------------------------------------------------------------------ tasks
  tasks: (params?: { project_id?: string; status?: string; limit?: number }) =>
    request<Page<Task>>('/tasks', { params }),

  task: (id: string) => request<Task>(`/tasks/${id}`),

  createTask: (input: {
    project_id: string;
    name: string;
    description?: string;
    segment_size?: number;
  }) => request<Task>('/tasks', { method: 'POST', body: input }),

  deleteTask: (id: string) => request<void>(`/tasks/${id}`, { method: 'DELETE' }),

  uploadAssets: (taskId: string, files: File[]) => {
    const form = new FormData();
    for (const file of files) form.append('files', file);
    return request<{ id: string; name: string }[]>(`/tasks/${taskId}/assets`, {
      method: 'POST',
      formData: form,
    });
  },

  /**
   * Attach media that is already on this machine, without copying it.
   *
   * Desktop only: the server 404s this route unless it is running in local mode, because
   * on a shared instance a path names a file on the *server's* disk.
   */
  localImport: (taskId: string, input: { path: string; recursive?: boolean }) =>
    request<LocalImportResult>(`/tasks/${taskId}/local-import`, { method: 'POST', body: input }),

  taskJobs: (taskId: string) => request<Job[]>(`/tasks/${taskId}/jobs`),
  taskProgress: (taskId: string) => request<TaskProgress>(`/tasks/${taskId}/progress`),
  frameInfo: (taskId: string, frame: number) =>
    request<FrameInfo>(`/tasks/${taskId}/frames/${frame}`),

  /**
   * A frame's pixels.
   *
   * Fetched rather than handed to `<img src>`, because an `<img>` element cannot send an
   * `Authorization` header and the media endpoint requires one — it enforces the same
   * permission check as the rest of the API rather than serving pixels from a public
   * bucket. The caller turns this into an object URL and revokes it when done.
   */
  frameBlob: (taskId: string, frame: number, signal?: AbortSignal) =>
    request<Blob>(`/tasks/${taskId}/frames/${frame}/data`, { signal }),

  /** How this task's frames are grouped into chunks, and how many there are. */
  taskMedia: (taskId: string) => request<TaskMediaMeta>(`/tasks/${taskId}/media`),

  /**
   * A run of decoded video frames, as one archive.
   *
   * The server decodes a chunk in a single pass, so asking for 36 frames this way costs
   * one decode rather than 36 partial ones — and one request rather than 36. See
   * `features/editor/chunks.ts` for what the editor does with it.
   */
  chunkArchive: async (taskId: string, chunk: number, signal?: AbortSignal) => {
    const blob = await request<Blob>(`/tasks/${taskId}/chunks/${chunk}`, { signal });
    return blob.arrayBuffer();
  },

  // ------------------------------------------------------------------------- jobs
  jobs: (params?: { mine?: boolean; state?: string; limit?: number }) =>
    request<Page<Job>>('/jobs', { params }),

  job: (id: string) => request<Job>(`/jobs/${id}`),

  updateJob: (id: string, changes: Partial<Pick<Job, 'state' | 'locked'>> & { assignee_id?: string }) =>
    request<Job>(`/jobs/${id}`, { method: 'PATCH', body: changes }),

  reviewJob: (id: string, accepted: boolean, comment?: string) =>
    request<Job>(`/jobs/${id}/review`, { method: 'POST', body: { accepted, comment } }),

  // ------------------------------------------------------------------ annotations
  annotations: (jobId: string) => request<AnnotationDocument>(`/jobs/${jobId}/annotations`),

  writeAnnotations: (jobId: string, batch: Record<string, unknown>) =>
    request<AnnotationWriteResult>(`/jobs/${jobId}/annotations`, {
      method: 'PATCH',
      body: batch,
    }),

  // ----------------------------------------------------------------------- review
  issues: (jobId: string) => request<Issue[]>(`/jobs/${jobId}/issues`),

  createIssue: (jobId: string, input: { frame: number; position?: number[]; body: string }) =>
    request<Issue>(`/jobs/${jobId}/issues`, { method: 'POST', body: input }),

  resolveIssue: (jobId: string, issueId: string) =>
    request<Issue>(`/jobs/${jobId}/issues/${issueId}`, {
      method: 'PATCH',
      body: { state: 'resolved' },
    }),

  // --------------------------------------------------------------------- datasets
  formats: () => request<DatasetFormat[]>('/formats'),

  exportProject: (projectId: string, body: { format: string; only_accepted?: boolean }) =>
    request<Blob>(`/projects/${projectId}/export`, { method: 'POST', body }),

  // ----------------------------------------------------------------------- models
  models: (params?: { organization_id?: string }) =>
    request<ModelRegistration[]>('/models', { params }),

  runInference: (
    jobId: string,
    body: {
      model_id: string;
      job_id: string;
      frames?: number[];
      label_mapping?: Record<string, string>;
      confidence_threshold?: number;
      persist?: boolean;
    },
  ) => request<InferenceResult>(`/jobs/${jobId}/inference`, { method: 'POST', body }),

  decideSuggestions: (jobId: string, shapeIds: string[], accepted: boolean) =>
    request<Record<string, number>>(`/jobs/${jobId}/suggestions`, {
      method: 'POST',
      body: { shape_ids: shapeIds, accepted },
    }),
};
