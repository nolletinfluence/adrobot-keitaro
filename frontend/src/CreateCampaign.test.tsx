import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CreateCampaign } from './CreateCampaign';
import { intentStorageKey, readIntent, writeIntent } from './creationIntent';
import { campaignFixture, catalogs, response } from './testing/fixtures';

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });

const props = () => ({ catalogs, onCreated: vi.fn(), onBusyChange: vi.fn(), onCancel: vi.fn(), onInspectOperation: vi.fn() });

async function fillForm() {
  await userEvent.type(screen.getByRole('textbox', { name: 'Название кампании' }), 'demo');
  await userEvent.type(screen.getByRole('textbox', { name: 'Код страны' }), 'KG');
  await userEvent.type(screen.getByRole('combobox', { name: 'Оффер для остального трафика' }), '11');
  await userEvent.click(await screen.findByRole('option', { name: /#11\s*Main offer/ }));
}

describe('creation recovery', () => {
  it('reuses the same idempotency key after a lost response', async () => {
    const submitted: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      if (url.startsWith('/api/offers')) return response({ items: [{ id: 11, name: 'Main offer' }], has_more: false });
      submitted.push(new Headers(init.headers).get('Idempotency-Key')!);
      if (submitted.length === 1) throw new TypeError('connection interrupted');
      return response(campaignFixture(), 201);
    }));
    const callbacks = props();
    render(<CreateCampaign {...callbacks} />);
    await fillForm();
    await userEvent.click(screen.getByRole('button', { name: 'Создать кампанию' }));
    await screen.findByRole('button', { name: 'Продолжить ту же попытку' });
    const intent = readIntent(intentStorageKey('live', 'test_borkoev_'));
    expect(intent?.key).toBe(submitted[0]);
    await userEvent.click(screen.getByRole('button', { name: 'Продолжить ту же попытку' }));
    await waitFor(() => expect(callbacks.onCreated).toHaveBeenCalledOnce());
    expect(submitted).toHaveLength(2);
    expect(submitted[0]).toBe(submitted[1]);
    expect(readIntent(intentStorageKey('live', 'test_borkoev_'))).toBeNull();
  });

  it('survives remount and reconciles without another campaign POST', async () => {
    let creates = 0;
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.startsWith('/api/offers')) return response({ items: [{ id: 11, name: 'Main offer' }], has_more: false });
      if (url === '/api/campaigns') {
        creates++;
        return response({ detail: 'Uncertain creation', code: 'OPERATION_RECONCILIATION_REQUIRED', operation_id: 'operation-1', outcome: 'unknown' }, 502);
      }
      if (url === '/api/operations/operation-1/reconcile') return response({ id: 'operation-1', kind: 'create_campaign', status: 'succeeded', campaign_id: 1 });
      return response(campaignFixture());
    }));
    const first = render(<CreateCampaign {...props()} />);
    await fillForm();
    await userEvent.click(screen.getByRole('button', { name: 'Создать кампанию' }));
    await screen.findByRole('button', { name: 'Проверить результат' });
    first.unmount();
    const callbacks = props();
    render(<CreateCampaign {...callbacks} />);
    expect((screen.getByRole('textbox', { name: 'Название кампании' }) as HTMLInputElement).matches(':disabled')).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: 'Проверить результат' }));
    await waitFor(() => expect(callbacks.onCreated).toHaveBeenCalledOnce());
    expect(creates).toBe(1);
  });

  it('does not send a creation if its recovery key cannot be persisted', async () => {
    const fetcher = vi.fn(async (_url: RequestInfo | URL) => response({ items: [{ id: 11, name: 'Main offer' }], has_more: false }));
    vi.stubGlobal('fetch', fetcher);
    render(<CreateCampaign {...props()} />);
    await fillForm();
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('storage disabled', 'QuotaExceededError'); });
    await userEvent.click(screen.getByRole('button', { name: 'Создать кампанию' }));
    await screen.findByRole('alert');
    expect(fetcher.mock.calls.every((call) => String(call[0]).startsWith('/api/offers'))).toBe(true);
  });

  it.each(['failed', 'uncertain'] as const)('allows reset only when reconciliation is confirmed failed (%s)', async (status) => {
    writeIntent(intentStorageKey('live', 'test_borkoev_'), { version: 1, key: crypto.randomUUID(), payload: { name: 'test_borkoev_demo', country_code: 'KG', offer_id: 11, domain_id: 1, group_id: 2, source_id: 3 }, offer: { id: 11, name: 'Main offer' }, operationId: 'operation-1' });
    vi.stubGlobal('fetch', vi.fn(async () => response({ id: 'operation-1', status, detail: status === 'failed' ? 'Campaign archived' : 'Still unknown' })));
    render(<CreateCampaign {...props()} />);
    await userEvent.click(screen.getByRole('button', { name: 'Проверить результат' }));
    if (status === 'failed') {
      await userEvent.click(await screen.findByRole('button', { name: 'Изменить параметры' }));
      expect((screen.getByRole('textbox', { name: 'Название кампании' }) as HTMLInputElement).matches(':disabled')).toBe(false);
      expect(readIntent(intentStorageKey('live', 'test_borkoev_'))).toBeNull();
    } else {
      await screen.findByText('Still unknown');
      expect(screen.queryByRole('button', { name: 'Изменить параметры' })).toBeNull();
      expect((screen.getByRole('textbox', { name: 'Название кампании' }) as HTMLInputElement).matches(':disabled')).toBe(true);
      expect(readIntent(intentStorageKey('live', 'test_borkoev_'))).not.toBeNull();
    }
  });
});
