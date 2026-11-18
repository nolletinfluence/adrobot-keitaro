import { useState } from 'react';
import type { FormEvent } from 'react';
import { OfferPicker } from './OfferPicker';
import { messageOf, send } from './api';
import type { Campaign, Catalogs, Option } from './api';

type Props = { catalogs: Catalogs; onCreated: (campaign: Campaign) => void; onCancel: () => void; onBusyChange: (busy: boolean) => void };

function CatalogSelect({ id, label, value, onChange, options }: { id: string; label: string; value: string; onChange: (value: string) => void; options: Option[] }) {
  return <div className="field"><label htmlFor={id}>{label}</label><select id={id} required value={value} onChange={(event) => onChange(event.target.value)}>
    {options.length === 0 ? <option value="">Нет доступных значений</option> : null}
    {options.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
  </select></div>;
}

export function CreateCampaign({ catalogs, onCreated, onCancel, onBusyChange }: Props) {
  const [name, setName] = useState('');
  const [country, setCountry] = useState('');
  const [offer, setOffer] = useState<Option | null>(null);
  const [domain, setDomain] = useState(String(catalogs.domains[0]?.id ?? ''));
  const [group, setGroup] = useState(String(catalogs.groups[0]?.id ?? ''));
  const [source, setSource] = useState(String(catalogs.sources[0]?.id ?? ''));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!offer || busy) return;
    setBusy(true); onBusyChange(true); setError('');
    try {
      const campaign = await send<Campaign>('/campaigns', {
        name: `${catalogs.campaign_prefix}${name.trim()}`,
        country_code: country.trim().toUpperCase(),
        offer_id: offer.id,
        domain_id: Number(domain), group_id: Number(group), source_id: Number(source),
      });
      onCreated(campaign);
    } catch (reason) { setError(messageOf(reason)); }
    finally { setBusy(false); onBusyChange(false); }
  }

  return <section className="create-panel">
    <header className="workspace-heading"><div><h1>Новая кампания</h1><p>Создайте кампанию и два маршрута трафика</p></div></header>
    <form onSubmit={submit} aria-busy={busy}>
      <fieldset disabled={busy}>
        <legend className="sr-only">Параметры кампании</legend>
        <div className="field"><label htmlFor="campaign-name">Название кампании</label>
          <div className="prefixed-input"><span>{catalogs.campaign_prefix}</span><input id="campaign-name" required maxLength={100} value={name} onChange={(event) => setName(event.target.value)} placeholder="summer_sale" /></div>
        </div>
        <div className="form-grid">
          <div className="field"><label htmlFor="country">Код страны</label><input id="country" required pattern="[A-Za-z]{2}" minLength={2} maxLength={2} value={country} onChange={(event) => setCountry(event.target.value.toUpperCase())} placeholder="Например, KG" aria-describedby="country-help" /><span className="field-help" id="country-help">Двухбуквенный код ISO 3166-1</span></div>
          <OfferPicker label="Оффер для остального трафика" value={offer} onChange={setOffer} disabled={busy} />
        </div>
        <div className="form-grid form-grid-three">
          <CatalogSelect id="domain" label="Домен" value={domain} onChange={setDomain} options={catalogs.domains} />
          <CatalogSelect id="group" label="Группа" value={group} onChange={setGroup} options={catalogs.groups} />
          <CatalogSelect id="source" label="Источник" value={source} onChange={setSource} options={catalogs.sources} />
        </div>
        <div className="route-preview" aria-label="Маршруты новой кампании">
          <div><span className="route-step">1</span><span>Страна <strong>{country || '—'}</strong></span><span className="route-destination">Google</span></div>
          <div><span className="route-step">2</span><span>Остальные посетители</span><span className="route-destination">{offer ? `Оффер #${offer.id}` : 'Выберите оффер'}</span></div>
        </div>
      </fieldset>
      {error ? <p role="alert" className="notice notice-error">{error}</p> : null}
      <div className="form-actions"><button className="button button-primary" disabled={busy || !offer || !domain || !group || !source} type="submit">{busy ? 'Создаём кампанию…' : 'Создать кампанию'}</button><button className="button" disabled={busy} type="button" onClick={onCancel}>Отмена</button></div>
    </form>
  </section>;
}
