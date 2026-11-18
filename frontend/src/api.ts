export type Option = { id: number; name: string };

export type Catalogs = {
  domains: Option[];
  groups: Option[];
  sources: Option[];
  campaign_prefix: string;
  mode: 'live' | 'demo';
};

export type Operation = {
  id: string;
  kind: 'create_campaign' | 'publish_stream';
  status: 'pending' | 'uncertain' | 'succeeded' | 'failed' | 'conflict';
  phase: string;
  campaign_id: number | null;
  stream_id: number | null;
  detail: string | null;
  error_code: string | null;
  created_at: string;
  updated_at: string;
};

export type StreamOffer = Option & { weight: number; pinned: boolean; removed: boolean };

export type Stream = {
  id: number;
  campaign_id: number;
  name: string;
  editable: boolean;
  dirty: boolean;
  version: number;
  offers: StreamOffer[];
  total_weight: number;
  synced_at: string | null;
  published_offers: StreamOffer[];
  operation: Operation | null;
};

export type Campaign = Option & { tracking_url: string | null; streams: Stream[] };

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code = 'REQUEST_FAILED',
    public operationId: string | null = null,
    public outcome: 'not_applied' | 'unknown' | 'applied' | null = null,
    public requestId: string | null = null,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

function errorText(data: unknown): string {
  if (typeof data !== 'object' || data === null || !('detail' in data)) return 'Не удалось выполнить запрос';
  const detail = data.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      const field = Array.isArray(item.loc) ? item.loc.filter((part: unknown) => part !== 'body').join('.') : '';
      return `${field ? `${field}: ` : ''}${item.msg ?? 'Некорректное значение'}`;
    }).join('; ');
  }
  return 'Не удалось выполнить запрос';
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(new Error('timeout')), 90_000);
  const external = options.signal;
  const abort = () => controller.abort(external?.reason);
  if (external?.aborted) abort();
  external?.addEventListener('abort', abort, { once: true });
  try {
    const headers = new Headers(options.headers);
    if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    const response = await fetch(`/api${path}`, {
      ...options,
      signal: controller.signal,
      headers,
    });
    const data: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const envelope = data && typeof data === 'object' ? data as Record<string, unknown> : {};
      throw new ApiError(
        response.status,
        errorText(data),
        typeof envelope.code === 'string' ? envelope.code : 'REQUEST_FAILED',
        typeof envelope.operation_id === 'string' ? envelope.operation_id : response.headers.get('X-Operation-ID'),
        ['not_applied', 'unknown', 'applied'].includes(String(envelope.outcome)) ? envelope.outcome as ApiError['outcome'] : null,
        response.headers.get('X-Request-ID'),
      );
    }
    return data as T;
  } catch (error) {
    if (external?.aborted) throw new DOMException('Aborted', 'AbortError');
    if (error instanceof ApiError) throw error;
    if (controller.signal.aborted) throw new Error('Сервер долго не отвечает. Обновите данные перед повторной попыткой');
    throw new Error('Нет связи с сервером. Проверьте соединение и повторите запрос');
  } finally {
    window.clearTimeout(timer);
    external?.removeEventListener('abort', abort);
  }
}

export function send<T>(path: string, body: unknown, method = 'POST', idempotencyKey?: string) {
  return api<T>(path, { method, body: JSON.stringify(body), headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined });
}

export function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Не удалось выполнить действие';
}
