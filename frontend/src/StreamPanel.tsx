import { useRef, useState } from 'react';
import { OfferPicker } from './OfferPicker';
import { ApiError, api, send } from './api';
import type { Campaign, Operation, Option, Stream, StreamOffer } from './api';
import { DraftChanges } from './DraftChanges';

export type Mutate = (label: string, operation: () => Promise<Stream>) => Promise<boolean>;

function WeightInput({ offer, disabled, onSave }: { offer: StreamOffer; disabled: boolean; onSave: (weight: number) => Promise<boolean> }) {
  const [value, setValue] = useState(String(offer.weight));
  const [invalid, setInvalid] = useState(false);
  return <div className="weight-control"><input
    type="number" min={0} max={100} step={1}
    aria-label={`Доля оффера ${offer.name}`}
    title="Изменение доли закрепляет её"
    value={value} disabled={disabled}
    aria-invalid={invalid}
    onChange={(event) => { setValue(event.target.value); setInvalid(false); }}
    onBlur={async () => {
      const weight = Number(value);
      if (!value.trim() || !Number.isInteger(weight) || weight < 0 || weight > 100) { setInvalid(true); return; }
      if (weight !== offer.weight) {
        const saved = await onSave(weight);
        if (!saved) setValue(String(offer.weight));
      }
    }}
    onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
  /><span>%</span>{invalid ? <span className="sr-only">Введите целое число от 0 до 100</span> : null}</div>;
}

export function StreamPanel({ stream, busy, mutate, onInspectOperation }: { stream: Stream; busy: boolean; mutate: Mutate; onInspectOperation?: (id: string) => void }) {
  const [selected, setSelected] = useState<Option | null>(null);
  const publishAttempt = useRef<{ version: number; key: string } | null>(null);
  const active = stream.offers.filter((offer) => !offer.removed);
  const archived = stream.offers.filter((offer) => offer.removed);
  const frozen = busy || Boolean(stream.operation);

  async function publish() {
    if (publishAttempt.current?.version !== stream.version) publishAttempt.current = { version: stream.version, key: crypto.randomUUID() };
    const key = publishAttempt.current.key;
    await mutate('Изменения опубликованы в Keitaro', async () => {
      try { return await send<Stream>(`/streams/${stream.id}/publish`, { version: stream.version }, 'POST', key); }
      catch (error) {
        if (error instanceof ApiError && error.outcome === 'not_applied' && publishAttempt.current?.key === key) publishAttempt.current = null;
        throw error;
      }
    });
  }

  async function reconcile() {
    if (!stream.operation) return;
    const operationId = stream.operation.id;
    await mutate('Результат публикации подтверждён', async () => {
      const operation = await send<Operation>(`/operations/${operationId}/reconcile`, {});
      if (operation.status !== 'succeeded') throw new ApiError(409, operation.detail ?? 'Результат пока не подтверждён', operation.status === 'conflict' ? 'REMOTE_CONFLICT' : 'OPERATION_UNCERTAIN', operation.id, 'unknown');
      const campaign = await api<Campaign>(`/campaigns/${stream.campaign_id}`);
      const updated = campaign.streams.find((item) => item.id === stream.id);
      if (!updated) throw new Error('Поток больше не найден в кампании');
      return updated;
    });
  }

  async function add() {
    if (!selected) return;
    const existing = stream.offers.find((offer) => offer.id === selected.id);
    const success = await mutate('Оффер добавлен в черновик', () => existing?.removed
      ? send<Stream>(`/streams/${stream.id}/offers/${selected.id}`, { version: stream.version, removed: false }, 'PATCH')
      : send<Stream>(`/streams/${stream.id}/offers`, { version: stream.version, offer_id: selected.id }));
    if (success) setSelected(null);
  }

  function patch(offer: StreamOffer, changes: { removed?: boolean; pinned?: boolean; weight?: number }, message: string) {
    return mutate(message, () => send<Stream>(`/streams/${stream.id}/offers/${offer.id}`, { version: stream.version, ...changes }, 'PATCH'));
  }

  return <section className={`stream-panel${stream.dirty ? ' is-dirty' : ''}`} aria-labelledby={`stream-${stream.id}`}>
    <header className="stream-heading"><div><h2 id={`stream-${stream.id}`}>{stream.name}</h2><span className="stream-meta mono">Поток #{stream.id}</span></div>
      <span className={`stream-state${stream.dirty || stream.operation ? ' state-draft' : ''}`}>{stream.operation ? 'Результат не подтверждён' : stream.dirty ? 'Черновик' : 'Опубликован'}</span>
    </header>
    {!stream.editable ? <div className="opaque-stream"><p>Маршрут без офферов</p><span>Фильтры и перенаправление настраиваются в Keitaro</span></div> : <>
      <div className="distribution-heading"><span>Офферов в ротации: {active.length}</span><strong className="mono">{stream.total_weight}% трафика</strong></div>
      <div className="distribution" role="img" aria-label={active.map((offer) => `${offer.name}: ${offer.weight}%${offer.pinned ? ', закреплено' : ''}`).join('; ')}>
        {active.filter((offer) => offer.weight > 0).map((offer) => <span key={offer.id} className={offer.pinned ? 'distribution-pinned' : ''} style={{ width: `${offer.weight}%` }} title={`${offer.name}: ${offer.weight}%`} />)}
      </div>
      <div className="offer-table-wrapper"><table className="offer-table"><thead><tr><th scope="col">Оффер</th><th scope="col">Доля</th><th scope="col"><span className="sr-only">Действия</span></th></tr></thead><tbody>
        {[...active, ...archived].map((offer) => <tr key={offer.id} className={offer.removed ? 'is-removed' : ''}>
          <td><span className="offer-name">{offer.name}</span><span className="offer-id mono">#{offer.id}{offer.removed ? ' · в архиве' : ''}</span></td>
          <td>{offer.removed ? <span className="removed-weight mono">0%</span> : <WeightInput key={`${offer.id}-${stream.version}`} offer={offer} disabled={frozen} onSave={(weight) => patch(offer, { weight, pinned: true }, 'Доля закреплена в черновике')} />}</td>
          <td><div className="row-actions">{offer.removed
            ? <button className="text-button" disabled={frozen} onClick={() => patch(offer, { removed: false }, 'Оффер возвращён в черновик')}>Вернуть</button>
            : <><button className={`pin-button${offer.pinned ? ' is-pinned' : ''}`} aria-pressed={offer.pinned} disabled={frozen} onClick={() => patch(offer, { pinned: !offer.pinned }, offer.pinned ? 'Доля откреплена' : 'Доля закреплена')} title={offer.pinned ? 'Открепить долю' : 'Не менять долю при пересчёте'}>{offer.pinned ? 'Закреплено' : 'Закрепить'}</button><button className="text-button danger-button" disabled={frozen} onClick={() => patch(offer, { removed: true }, 'Оффер убран в архив черновика')}>Убрать</button></>}
          </div></td>
        </tr>)}
      </tbody></table></div>
      <div className="add-offer"><OfferPicker label={`Добавить оффер в «${stream.name}»`} value={selected} onChange={setSelected} disabled={frozen} excludeIds={active.map((offer) => offer.id)} /><button className="button" type="button" disabled={frozen || !selected} onClick={() => void add()}>Добавить</button></div>
      <DraftChanges stream={stream} />
      {stream.operation ? <div className="publication-recovery"><p>Keitaro мог применить изменения. Проверим результат перед следующей правкой</p>{onInspectOperation ? <button className="text-button" onClick={() => onInspectOperation(stream.operation!.id)}>Открыть операцию в журнале</button> : null}</div> : null}
      <footer className="stream-footer"><div><p>{stream.operation ? 'Ожидается подтверждение результата' : stream.dirty ? 'Черновик сохранён локально' : 'Распределение сохранено в Keitaro'}</p>{stream.synced_at ? <time className="sync-time" dateTime={stream.synced_at}>Синхронизация: {new Date(stream.synced_at).toLocaleTimeString('ru-RU')}</time> : null}</div><div className="stream-actions"><button className="button" disabled={frozen || !stream.dirty} onClick={() => void mutate('Черновик отменён', () => send<Stream>(`/streams/${stream.id}/cancel`, { version: stream.version }))}>Отменить</button>{stream.operation ? <button className="button button-primary" disabled={busy} onClick={() => void reconcile()}>Проверить результат</button> : <button className="button button-primary" disabled={busy || !stream.dirty} onClick={() => void publish()}>Опубликовать</button>}</div></footer>
    </>}
  </section>;
}
