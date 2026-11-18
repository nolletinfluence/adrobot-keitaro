import { useState } from 'react';
import { OfferPicker } from './OfferPicker';
import { send } from './api';
import type { Option, Stream, StreamOffer } from './api';

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

export function StreamPanel({ stream, busy, mutate }: { stream: Stream; busy: boolean; mutate: Mutate }) {
  const [selected, setSelected] = useState<Option | null>(null);
  const active = stream.offers.filter((offer) => !offer.removed);
  const archived = stream.offers.filter((offer) => offer.removed);

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
      <span className={`stream-state${stream.dirty ? ' state-draft' : ''}`}>{stream.dirty ? 'Черновик' : 'Опубликован'}</span>
    </header>
    {!stream.editable ? <div className="opaque-stream"><p>Маршрут без офферов</p><span>Фильтры и перенаправление настраиваются в Keitaro</span></div> : <>
      <div className="distribution-heading"><span>Офферов в ротации: {active.length}</span><strong className="mono">{stream.total_weight}% трафика</strong></div>
      <div className="distribution" role="img" aria-label={active.map((offer) => `${offer.name}: ${offer.weight}%${offer.pinned ? ', закреплено' : ''}`).join('; ')}>
        {active.filter((offer) => offer.weight > 0).map((offer) => <span key={offer.id} className={offer.pinned ? 'distribution-pinned' : ''} style={{ width: `${offer.weight}%` }} title={`${offer.name}: ${offer.weight}%`} />)}
      </div>
      <div className="offer-table-wrapper"><table className="offer-table"><thead><tr><th scope="col">Оффер</th><th scope="col">Доля</th><th scope="col"><span className="sr-only">Действия</span></th></tr></thead><tbody>
        {[...active, ...archived].map((offer) => <tr key={offer.id} className={offer.removed ? 'is-removed' : ''}>
          <td><span className="offer-name">{offer.name}</span><span className="offer-id mono">#{offer.id}{offer.removed ? ' · в архиве' : ''}</span></td>
          <td>{offer.removed ? <span className="removed-weight mono">0%</span> : <WeightInput key={`${offer.id}-${stream.version}`} offer={offer} disabled={busy} onSave={(weight) => patch(offer, { weight, pinned: true }, 'Доля закреплена в черновике')} />}</td>
          <td><div className="row-actions">{offer.removed
            ? <button className="text-button" disabled={busy} onClick={() => patch(offer, { removed: false }, 'Оффер возвращён в черновик')}>Вернуть</button>
            : <><button className={`pin-button${offer.pinned ? ' is-pinned' : ''}`} aria-pressed={offer.pinned} disabled={busy} onClick={() => patch(offer, { pinned: !offer.pinned }, offer.pinned ? 'Доля откреплена' : 'Доля закреплена')} title={offer.pinned ? 'Открепить долю' : 'Не менять долю при пересчёте'}>{offer.pinned ? 'Закреплено' : 'Закрепить'}</button><button className="text-button danger-button" disabled={busy} onClick={() => patch(offer, { removed: true }, 'Оффер убран в архив черновика')}>Убрать</button></>}
          </div></td>
        </tr>)}
      </tbody></table></div>
      <div className="add-offer"><OfferPicker label={`Добавить оффер в «${stream.name}»`} value={selected} onChange={setSelected} disabled={busy} excludeIds={active.map((offer) => offer.id)} /><button className="button" type="button" disabled={busy || !selected} onClick={() => void add()}>Добавить</button></div>
      <footer className="stream-footer"><p>{stream.dirty ? 'Изменения сохранены локально и ещё не отправлены в Keitaro' : 'Распределение сохранено в Keitaro'}</p><div className="stream-actions"><button className="button" disabled={busy || !stream.dirty} onClick={() => void mutate('Черновик отменён', () => send<Stream>(`/streams/${stream.id}/cancel`, { version: stream.version }))}>Отменить</button><button className="button button-primary" disabled={busy || !stream.dirty} onClick={() => void mutate('Изменения опубликованы в Keitaro', () => send<Stream>(`/streams/${stream.id}/publish`, { version: stream.version }))}>Опубликовать</button></div></footer>
    </>}
  </section>;
}
