export type Option = { id: number; name: string };

export type Catalogs = {
  domains: Option[];
  groups: Option[];
  sources: Option[];
  campaign_prefix: string;
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
};

export type Campaign = Option & { tracking_url: string | null; streams: Stream[] };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
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
    const response = await fetch(`/api${path}`, {
      ...options,
      signal: controller.signal,
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
    });
    const data: unknown = await response.json().catch(() => null);
    if (!response.ok) throw new ApiError(response.status, errorText(data));
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

export function send<T>(path: string, body: unknown, method = 'POST') {
  return api<T>(path, { method, body: JSON.stringify(body) });
}

export function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Не удалось выполнить действие';
}
