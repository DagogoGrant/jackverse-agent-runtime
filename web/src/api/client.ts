import { ProblemDetail } from './types';

export class ApiError extends Error {
  public problem?: ProblemDetail;
  public status: number;

  constructor(message: string, status: number, problem?: ProblemDetail) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.problem = problem;
  }
}

export class PreconditionFailedError extends ApiError {
  constructor(detail?: string) {
    super(
      detail || "This changed elsewhere. We've loaded the latest version.",
      412
    );
    this.name = 'PreconditionFailedError';
  }
}

export class PreconditionRequiredError extends ApiError {
  constructor(detail?: string) {
    super(detail || 'Precondition required. An ETag must be supplied.', 428);
    this.name = 'PreconditionRequiredError';
  }
}

export const isDevAuthEnabled = (): boolean => {
  return import.meta.env.VITE_JACKVERSE_DEV_AUTH === 'true';
};

export const getDevUser = (): string => {
  if (!isDevAuthEnabled()) return '';
  return localStorage.getItem('jv_dev_user') || 'alice';
};

export const setDevUser = (user: string) => {
  if (!isDevAuthEnabled()) return;
  localStorage.setItem('jv_dev_user', user);
  window.dispatchEvent(new CustomEvent('jv:dev_user_change', { detail: { user } }));
};

const getBaseUrl = (): string => {
  return '/api/v1';
};

export interface ApiResponse<T> {
  data: T;
  etag: string | null;
}

export async function apiRequest<T>(
  endpoint: string,
  options: RequestInit = {},
  expectedETag?: string | null
): Promise<ApiResponse<T>> {
  const url = `${getBaseUrl()}${endpoint}`;
  const headers = new Headers(options.headers || {});

  // Dev auth header injection ONLY when explicitly enabled
  if (isDevAuthEnabled()) {
    const devUser = getDevUser();
    if (devUser) {
      headers.set('X-JackVerse-User', devUser);
    }
  }

  // Precondition matching
  if (expectedETag) {
    headers.set('If-Match', expectedETag);
  }

  if (!headers.has('Content-Type') && options.body && typeof options.body === 'string') {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  const etag = response.headers.get('ETag');

  if (!response.ok) {
    let problem: ProblemDetail | undefined;
    try {
      const errJson = await response.json();
      problem = errJson as ProblemDetail;
    } catch {
      // not JSON
    }

    if (response.status === 412) {
      throw new PreconditionFailedError(problem?.detail);
    }
    if (response.status === 428) {
      throw new PreconditionRequiredError(problem?.detail);
    }

    const errorMsg = problem?.detail || problem?.title || `Request failed with status ${response.status}`;
    throw new ApiError(errorMsg, response.status, problem);
  }

  if (response.status === 204) {
    return { data: null as unknown as T, etag };
  }

  const data = (await response.json()) as T;
  return { data, etag };
}
