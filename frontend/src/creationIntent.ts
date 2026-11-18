import type { Option } from './api';

export type CreationPayload = { name: string; country_code: string; offer_id: number; domain_id: number; group_id: number; source_id: number };
export type CreationIntent = { version: 1; key: string; payload: CreationPayload; offer: Option; operationId: string | null };

export function intentStorageKey(mode: string, prefix: string): string {
  return `adrobot:create:v1:${mode}:${prefix}`;
}

export function readIntent(storageKey: string): CreationIntent | null {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) ?? 'null');
    if (!value || value.version !== 1 || typeof value.key !== 'string' || !/^[0-9a-f-]{36}$/i.test(value.key) || !value.payload || !value.offer || !(value.operationId === null || typeof value.operationId === 'string')) return null;
    const payload = value.payload;
    if (typeof payload.name !== 'string' || typeof payload.country_code !== 'string' || !/^[A-Z]{2}$/i.test(payload.country_code) || ![payload.offer_id, payload.domain_id, payload.group_id, payload.source_id, value.offer.id].every((id) => Number.isSafeInteger(id) && id > 0) || value.offer.id !== payload.offer_id || typeof value.offer.name !== 'string') return null;
    return value as CreationIntent;
  } catch { return null; }
}

export function writeIntent(storageKey: string, intent: CreationIntent | null): void {
  if (intent) localStorage.setItem(storageKey, JSON.stringify(intent));
  else localStorage.removeItem(storageKey);
}
