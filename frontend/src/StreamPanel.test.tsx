import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StreamPanel } from './StreamPanel';
import type { Stream } from './api';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const stream: Stream = {
  id: 11, campaign_id: 1, name: 'Офферы', editable: true, dirty: true, version: 7, total_weight: 100, synced_at: null,
  offers: [
    { id: 1, name: 'Первый оффер', weight: 100, pinned: true, removed: false },
    { id: 2, name: 'Архивный оффер', weight: 0, pinned: false, removed: true },
  ],
};

describe('stream editing controls', () => {
  it('restores an archived binding through a local draft update with the current version', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(stream), { status: 200 }));
    vi.stubGlobal('fetch', fetcher);
    render(<StreamPanel stream={stream} busy={false} mutate={async (_message, operation) => { await operation(); return true; }} />);
    await userEvent.click(screen.getByRole('button', { name: 'Вернуть' }));
    expect(fetcher).toHaveBeenCalledWith('/api/streams/11/offers/2', expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ version: 7, removed: false }) }));
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('keeps non-offer streams opaque and does not expose write controls', () => {
    render(<StreamPanel stream={{ ...stream, editable: false, offers: [], dirty: false }} busy={false} mutate={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Опубликовать' })).toBeNull();
    expect(screen.getByText('Маршрут без офферов')).toBeDefined();
  });

  it('prevents duplicate publishes while another operation is in flight', () => {
    render(<StreamPanel stream={stream} busy={true} mutate={vi.fn()} />);
    expect((screen.getByRole('button', { name: 'Опубликовать' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Вернуть' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('restores the confirmed weight after the backend rejects an allocation', async () => {
    render(<StreamPanel stream={stream} busy={false} mutate={async () => false} />);
    const input = screen.getByRole('spinbutton', { name: 'Доля оффера Первый оффер' }) as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, '25');
    await userEvent.tab();
    await waitFor(() => expect(input.value).toBe('100'));
  });
});
