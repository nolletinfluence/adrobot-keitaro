import { describe, expect, it } from 'vitest';
import { describeChanges } from './DraftChanges';
import { streamFixture } from './testing/fixtures';

describe('publication preview', () => {
  it('describes added, removed, restored and reweighted offers against the published snapshot', () => {
    const stream = streamFixture({
      dirty: true,
      published_offers: [
        { id: 1, name: 'Removed', weight: 34, pinned: false, removed: false },
        { id: 2, name: 'Restored', weight: 0, pinned: false, removed: true },
        { id: 3, name: 'Pinned', weight: 66, pinned: false, removed: false },
      ],
      offers: [
        { id: 1, name: 'Removed', weight: 0, pinned: false, removed: true },
        { id: 2, name: 'Restored', weight: 25, pinned: false, removed: false },
        { id: 3, name: 'Pinned', weight: 25, pinned: true, removed: false },
        { id: 4, name: 'Added', weight: 50, pinned: false, removed: false },
      ],
    });
    expect(describeChanges(stream)).toEqual([
      { id: 1, name: 'Removed', description: 'Убрать из ротации · 34% → 0%' },
      { id: 2, name: 'Restored', description: 'Вернуть из архива · 25%' },
      { id: 3, name: 'Pinned', description: '66% → 25% · Закрепить долю' },
      { id: 4, name: 'Added', description: 'Добавить · 50%' },
    ]);
  });
});
