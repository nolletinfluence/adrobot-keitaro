import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from './App';
import { campaignFixture, catalogs, response, streamFixture } from './testing/fixtures';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.location.hash = ''; });

it('does not apply a late mutation message to another campaign after browser navigation', async () => {
  let finish: (response: Response) => void = () => undefined;
  const pending = new Promise<Response>((resolve) => { finish = resolve; });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url === '/api/catalogs') return response(catalogs);
    if (url.startsWith('/api/campaigns?q=')) return response({ items: [campaignFixture(1), campaignFixture(2)] });
    if (url === '/api/campaigns/1') return response(campaignFixture(1));
    if (url === '/api/campaigns/2') return response(campaignFixture(2));
    return pending;
  }));
  window.location.hash = 'campaign=1';
  render(<App />);
  await screen.findByRole('heading', { name: 'test_borkoev_campaign_1' });
  await userEvent.click(screen.getByRole('button', { name: 'Закрепить' }));
  await act(async () => {
    window.location.hash = 'campaign=2';
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
  await screen.findByRole('heading', { name: 'test_borkoev_campaign_2' });
  await act(async () => { finish(response(streamFixture({ id: 11, dirty: true, version: 2 }))); });
  await waitFor(() => expect(screen.queryByText('Доля закреплена')).toBeNull());
  expect(screen.getByRole('heading', { name: 'test_borkoev_campaign_2' })).toBeDefined();
});
