import { useState } from 'react';
import type { FormEvent } from 'react';
import { OfferPicker } from './OfferPicker';
import { ErrorNotice } from './ErrorNotice';
import { ApiError, api, send } from './api';
import type { Campaign, Catalogs, Operation, Option } from './api';
import { intentStorageKey, readIntent, writeIntent } from './creationIntent';
import type { CreationIntent } from './creationIntent';

type Props = {
  catalogs: Catalogs;
  onCreated: (campaign: Campaign) => void;
  onCancel: () => void;
  onBusyChange: (busy: boolean) => void;
  onInspectOperation: (id: string) => void;
};

function CatalogSelect({ id, label, value, onChange, options }: {
  id: string; label: string; value: string; onChange: (value: string) => void; options: Option[];
}) {
  return <div className="field"><label htmlFor={id}>{label}</label>
    <select id={id} required value={value} onChange={(event) => onChange(event.target.value)}>
      {options.length === 0 ? <option value="">Нет доступных значений</option> : null}
      {options.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select>
  </div>;
}

export function CreateCampaign({ catalogs, onCreated, onCancel, onBusyChange, onInspectOperation }: Props) {
  const storageKey = intentStorageKey(catalogs.mode, catalogs.campaign_prefix);
  const [attempt, setAttempt] = useState<CreationIntent | null>(() => readIntent(storageKey));
  const [name, setName] = useState(() => attempt?.payload.name.slice(catalogs.campaign_prefix.length) ?? '');
  const [country, setCountry] = useState(() => attempt?.payload.country_code ?? '');
  const [offer, setOffer] = useState<Option | null>(() => attempt?.offer ?? null);
  const [domain, setDomain] = useState(() => String(attempt?.payload.domain_id ?? catalogs.domains[0]?.id ?? ''));
  const [group, setGroup] = useState(() => String(attempt?.payload.group_id ?? catalogs.groups[0]?.id ?? ''));
  const [source, setSource] = useState(() => String(attempt?.payload.source_id ?? catalogs.sources[0]?.id ?? ''));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [operation, setOperation] = useState<Operation | null>(null);

  function persist(intent: CreationIntent | null): boolean {
    try { writeIntent(storageKey, intent); return true; }
    catch { setError(new Error('Не удалось сохранить попытку в браузере. Разрешите локальное хранилище перед созданием кампании')); return false; }
  }

  function finished(campaign: Campaign) {
    persist(null);
    setAttempt(null);
    onCreated(campaign);
  }

  function resetFailedAttempt() {
    if (operation?.status !== 'failed' || busy) return;
    if (persist(null)) { setAttempt(null); setOperation(null); setError(null); }
  }

  async function reconcile() {
    if (!attempt?.operationId || busy) return;
    setBusy(true); onBusyChange(true); setError(null);
    try {
      const result = await send<Operation>(`/operations/${attempt.operationId}/reconcile`, {});
      setOperation(result);
      if (result.status === 'succeeded' && result.campaign_id) finished(await api<Campaign>(`/campaigns/${result.campaign_id}`));
    } catch (reason) { setError(reason); }
    finally { setBusy(false); onBusyChange(false); }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!offer || busy) return;
    if (attempt?.operationId) { await reconcile(); return; }
    if (!name.trim()) { setError(new Error('Укажите название кампании')); return; }
    const intent: CreationIntent = attempt ?? {
      version: 1,
      key: crypto.randomUUID(),
      payload: { name: `${catalogs.campaign_prefix}${name.trim()}`, country_code: country.trim().toUpperCase(), offer_id: offer.id, domain_id: Number(domain), group_id: Number(group), source_id: Number(source) },
      offer,
      operationId: null,
    };
    if (!persist(intent)) return;
    setAttempt(intent); setBusy(true); onBusyChange(true); setError(null);
    try {
      finished(await send<Campaign>('/campaigns', intent.payload, 'POST', intent.key));
    } catch (reason) {
      if (reason instanceof ApiError && (reason.outcome === 'not_applied' || (reason.status === 422 && !reason.operationId))) {
        if (persist(null)) setAttempt(null);
      } else if (reason instanceof ApiError && reason.operationId) {
        const updated = { ...intent, operationId: reason.operationId };
        setAttempt(updated);
        persist(updated);
      }
      setError(reason);
    } finally { setBusy(false); onBusyChange(false); }
  }

  return <section className="create-panel">
    <header className="workspace-heading"><div><h1>Новая кампания</h1><p>Создайте кампанию и два маршрута трафика</p></div></header>
    {attempt ? <div className="notice notice-pending"><p>Сохранена попытка создания</p><p>Её ключ переживает обновление страницы. Продолжение использует ту же попытку и защищает от дубликатов</p>{attempt.operationId ? <button className="text-button" onClick={() => onInspectOperation(attempt.operationId!)}>Открыть в журнале</button> : null}</div> : null}
    <form onSubmit={submit} aria-busy={busy}>
      <fieldset disabled={busy || Boolean(attempt)}>
        <legend className="sr-only">Параметры кампании</legend>
        <div className="field"><label htmlFor="campaign-name">Название кампании</label><div className="prefixed-input"><span>{catalogs.campaign_prefix}</span><input id="campaign-name" required maxLength={100} value={name} onChange={(event) => setName(event.target.value)} placeholder="summer_sale" /></div></div>
        <div className="form-grid">
          <div className="field"><label htmlFor="country">Код страны</label><input id="country" required pattern="[A-Za-z]{2}" minLength={2} maxLength={2} value={country} onChange={(event) => setCountry(event.target.value.toUpperCase())} placeholder="Например, KG" aria-describedby="country-help" /><span className="field-help" id="country-help">Двухбуквенный код ISO 3166-1</span></div>
          <OfferPicker label="Оффер для остального трафика" value={offer} onChange={setOffer} disabled={busy || Boolean(attempt)} />
        </div>
        <div className="form-grid form-grid-three">
          <CatalogSelect id="domain" label="Домен" value={domain} onChange={setDomain} options={catalogs.domains} />
          <CatalogSelect id="group" label="Группа" value={group} onChange={setGroup} options={catalogs.groups} />
          <CatalogSelect id="source" label="Источник" value={source} onChange={setSource} options={catalogs.sources} />
        </div>
        <div className="route-preview" aria-label="Маршруты новой кампании"><div><span className="route-step">1</span><span>Страна <strong>{country || '—'}</strong></span><span className="route-destination">Google</span></div><div><span className="route-step">2</span><span>Остальные посетители</span><span className="route-destination">{offer ? `Оффер #${offer.id}` : 'Выберите оффер'}</span></div></div>
      </fieldset>
      {operation && operation.status !== 'succeeded' ? <p className="notice notice-pending" role="status">{operation.detail ?? 'Результат ещё не подтверждён. Проверьте операцию в журнале перед дальнейшими действиями'}</p> : null}
      {operation?.status === 'failed' ? <button className="button" type="button" disabled={busy} onClick={resetFailedAttempt}>Изменить параметры</button> : null}
      {error ? <ErrorNotice error={error} onInspect={onInspectOperation} /> : null}
      <div className="form-actions"><button className="button button-primary" disabled={busy || !offer || !domain || !group || !source} type="submit">{busy ? 'Проверяем создание…' : attempt?.operationId ? 'Проверить результат' : attempt ? 'Продолжить ту же попытку' : 'Создать кампанию'}</button><button className="button" disabled={busy} type="button" onClick={onCancel}>{attempt ? 'Вернуться к кампаниям' : 'Отмена'}</button></div>
    </form>
  </section>;
}
