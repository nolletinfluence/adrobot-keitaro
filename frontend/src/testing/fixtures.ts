import type { Campaign, Catalogs, Stream } from '../api';

export const catalogs: Catalogs = {
  domains: [{ id: 1, name: 'Test domain' }], groups: [{ id: 2, name: 'Test group' }],
  sources: [{ id: 3, name: 'Test source' }], campaign_prefix: 'test_borkoev_', mode: 'live',
};

export function streamFixture(overrides: Partial<Stream> = {}): Stream {
  const offers = [{ id: 11, name: 'Main offer', weight: 100, pinned: false, removed: false }];
  return { id: 21, campaign_id: 1, name: 'Offers', editable: true, dirty: false, version: 1, total_weight: 100, synced_at: null, offers, published_offers: structuredClone(offers), operation: null, ...overrides };
}

export function campaignFixture(id = 1): Campaign {
  return { id, name: `test_borkoev_campaign_${id}`, tracking_url: null, streams: [streamFixture({ id: id * 10 + 1, campaign_id: id })] };
}

export function response(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}
