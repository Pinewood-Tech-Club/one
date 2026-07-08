// Typed fetch layer for the Flask backend. All requests carry the session
// cookie; non-2xx responses throw ApiError with the backend's error code.

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? '';

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, code?: string, message?: string) {
    super(message ?? code ?? `Request failed with status ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(`${BACKEND_URL}${path}`, {
    ...init,
    credentials: 'include',
  });

  if (!response.ok) {
    let code: string | undefined;
    try {
      const body: unknown = await response.json();
      if (
        body &&
        typeof body === 'object' &&
        'error' in body &&
        typeof (body as { error: unknown }).error === 'string'
      ) {
        code = (body as { error: string }).error;
      }
    } catch {
      // non-JSON error body
    }
    throw new ApiError(response.status, code);
  }

  return response;
}

async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init);
  return (await response.json()) as T;
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
}

// ── Shared types ──────────────────────────────────────────────────────────────

export type OnboardingStep = 'welcome' | 'connect_lms' | 'smart_consent' | 'completed';

export type SmartFeaturesConsent = {
  enabled: boolean;
  timestamp: number;
  version: string;
};

export type ApiUser = {
  user_id: number;
  email: string;
  name: string;
  created_at: string;
  last_login: string;
  onboarding_step: OnboardingStep;
  schoology_connected: boolean;
  profile_picture_url: string | null;
  smart_features_consent: SmartFeaturesConsent | null;
};

export type ChatStatus = 'queued' | 'streaming' | 'completed' | 'failed' | 'cancelled';

export type ChatActivity =
  | 'thinking'
  | 'streaming_text'
  | 'tool_running'
  | 'post_tool_reasoning';

export type ChatThread = {
  _id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  lastMessageAt: number;
};

export type ChatMessage = {
  _id: string;
  threadId: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  status: ChatStatus;
  error: string | null;
  createdAt: number;
  updatedAt: number;
  completedAt: number | null;
};

export type ChatGeneration = {
  _id: string;
  threadId: string;
  status: ChatStatus;
  activity: ChatActivity | null;
  cancelRequested: boolean;
  createdAt: number;
  startedAt: number | null;
  updatedAt: number;
  completedAt?: number | null;
  errorCode?: string | null;
  errorMessage?: string | null;
};

// Merged Schoology assignment record: full API object plus the store's
// computed/override fields. Extra keys pass through untyped.
export type UpcomingAssignment = {
  id?: string | number;
  title?: string;
  due?: string;
  description?: string;
  course_title?: string;
  section_title?: string;
  section_id?: string;
  completed?: boolean;
  completion_status?: string | null;
  grade?: string | null;
  _courseId?: string;
  _lastUpdated?: number;
} & Record<string, unknown>;

export type SendChatMessageResult = {
  threadId: string;
  userMessageId: string;
  assistantMessageId: string;
  generationId: string;
  createdThread: boolean;
};

// ── Typed helpers ─────────────────────────────────────────────────────────────

export function getUser(): Promise<ApiUser> {
  return apiJson<ApiUser>('/api/user');
}

export function getUpcoming(): Promise<{ assignments: UpcomingAssignment[] }> {
  return apiJson<{ assignments: UpcomingAssignment[] }>('/api/schoology/upcoming');
}

export function getChatThreads(): Promise<{ threads: ChatThread[] }> {
  return apiJson<{ threads: ChatThread[] }>('/api/chat/threads');
}

export function getChatMessages(threadId: string): Promise<{ messages: ChatMessage[] }> {
  return apiJson<{ messages: ChatMessage[] }>(
    `/api/chat/threads/${encodeURIComponent(threadId)}/messages`,
  );
}

export function getActiveGeneration(
  threadId: string,
): Promise<{ generation: ChatGeneration | null }> {
  return apiJson<{ generation: ChatGeneration | null }>(
    `/api/chat/threads/${encodeURIComponent(threadId)}/active-generation`,
  );
}

export function sendChatMessage(body: {
  threadId?: string | null;
  clientRequestId: string;
  content: string;
}): Promise<SendChatMessageResult> {
  return apiJson<SendChatMessageResult>('/api/chat/messages', jsonInit('POST', body));
}

export function cancelGeneration(generationId: string): Promise<{ success: boolean }> {
  return apiJson<{ success: boolean }>(
    `/api/chat/generations/${encodeURIComponent(generationId)}/cancel`,
    { method: 'POST' },
  );
}

export function startOnboarding(): Promise<{
  success: boolean;
  step: OnboardingStep;
  user: ApiUser;
}> {
  return apiJson('/api/user/onboarding/start', { method: 'POST' });
}

export function saveConsent(body: { enabled: boolean; version: string }): Promise<{
  success: boolean;
  step: OnboardingStep;
  consent: SmartFeaturesConsent;
  user: ApiUser;
}> {
  return apiJson('/api/user/consent', jsonInit('POST', body));
}

export function postDeveloperOverride(body: {
  clientId: string;
  clientSecret: string;
}): Promise<{ success: boolean; user: ApiUser } & Record<string, unknown>> {
  return apiJson('/api/schoology/developer-override', jsonInit('POST', body));
}
