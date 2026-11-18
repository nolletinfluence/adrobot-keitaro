import type { Stream, StreamOffer } from './api';

export type OfferChange = { id: number; name: string; description: string };

export function describeChanges(stream: Stream): OfferChange[] {
  const before = new Map((stream.published_offers ?? []).map((offer) => [offer.id, offer]));
  return stream.offers.flatMap((after: StreamOffer) => {
    const previous = before.get(after.id);
    if (!previous) return [{ id: after.id, name: after.name, description: after.removed ? 'Новый оффер оставлен в архиве' : `Добавить · ${after.weight}%` }];
    if (!previous.removed && after.removed) return [{ id: after.id, name: after.name, description: `Убрать из ротации · ${previous.weight}% → 0%` }];
    if (previous.removed && !after.removed) return [{ id: after.id, name: after.name, description: `Вернуть из архива · ${after.weight}%` }];
    const parts = [];
    if (previous.weight !== after.weight) parts.push(`${previous.weight}% → ${after.weight}%`);
    if (previous.pinned !== after.pinned) parts.push(after.pinned ? 'Закрепить долю' : 'Снять закрепление');
    return parts.length ? [{ id: after.id, name: after.name, description: parts.join(' · ') }] : [];
  });
}

export function DraftChanges({ stream }: { stream: Stream }) {
  if (!stream.dirty) return null;
  const changes = describeChanges(stream);
  return <details className="draft-changes" open>
    <summary>Будет опубликовано: {changes.length}</summary>
    <ul>{changes.map((change) => <li key={change.id}><span>{change.name}</span><span>{change.description}</span></li>)}</ul>
    <p>Изменения применятся только после публикации этого потока</p>
  </details>;
}
