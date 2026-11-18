import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api } from './api';

afterEach(() => vi.unstubAllGlobals());

describe('API boundary', () => {
  it('preserves conflict details so the editor can explain why publishing failed', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Поток изменён в Keitaro' }), { status: 409 })));
    await expect(api('/streams/5/publish')).rejects.toEqual(new ApiError(409, 'Поток изменён в Keitaro'));
  });

  it('does not mistake cancellation for an upstream or connection failure', async () => {
    const controller = new AbortController();
    controller.abort();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new DOMException('Aborted', 'AbortError')));
    await expect(api('/offers', { signal: controller.signal })).rejects.toHaveProperty('name', 'AbortError');
  });

  it('renders validation details without displaying the server response as HTML', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: [{ loc: ['body', 'country_code'], msg: 'Invalid country' }] }), { status: 422 })));
    await expect(api('/campaigns')).rejects.toThrow('country_code: Invalid country');
  });
});
