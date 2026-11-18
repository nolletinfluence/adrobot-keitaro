import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api, messageOf, send } from './api';
import type { Campaign, Catalogs, Option } from './api';
import { CreateCampaign } from './CreateCampaign';
import { StreamPanel } from './StreamPanel';
import type { Mutate } from './StreamPanel';

function campaignFromHash(): number | null {
  const value = new URLSearchParams(window.location.hash.slice(1)).get('campaign');
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

function safeTrackingUrl(value: string | null): string | null {
  if (!value) return null;
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : null; }
  catch { return null; }
}

export default function App() {
  const [catalogs, setCatalogs] = useState<Catalogs | null>(null);
  const [campaigns, setCampaigns] = useState<Option[]>([]);
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(campaignFromHash);
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState('');
  const [listLoading, setListLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [retry, setRetry] = useState(0);
  const listSequence = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    api<Catalogs>('/catalogs', { signal: controller.signal }).then(setCatalogs).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(messageOf(reason));
    });
    return () => controller.abort();
  }, [retry]);

  useEffect(() => {
    const controller = new AbortController();
    const sequence = ++listSequence.current;
    setListLoading(true);
    const timer = window.setTimeout(() => {
      api<{ items: Option[] }>(`/campaigns?q=${encodeURIComponent(search)}`, { signal: controller.signal })
        .then((result) => { if (sequence === listSequence.current) setCampaigns(result.items); })
        .catch((reason: unknown) => { if (!controller.signal.aborted) setError(messageOf(reason)); })
        .finally(() => { if (!controller.signal.aborted && sequence === listSequence.current) setListLoading(false); });
    }, 200);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [search, retry]);

  useEffect(() => {
    function changed() { setSelectedId(campaignFromHash()); setCreating(false); }
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, []);

  useEffect(() => {
    if (!selectedId) { setCampaign(null); return; }
    const controller = new AbortController();
    let active = true;
    setBusy('Загружаем кампанию…'); setError(''); setNotice(''); setCampaign(null);
    async function load() {
      try {
        let result: Campaign;
        try { result = await api<Campaign>(`/campaigns/${selectedId}`, { signal: controller.signal }); }
        catch (reason) {
          if (!(reason instanceof ApiError) || reason.status !== 404) throw reason;
          result = await api<Campaign>(`/campaigns/${selectedId}/fetch`, { method: 'POST', body: JSON.stringify({ discard_draft: false }), signal: controller.signal });
        }
        if (active) setCampaign(result);
      } catch (reason) { if (active && !controller.signal.aborted) setError(messageOf(reason)); }
      finally { if (active) setBusy(''); }
    }
    void load();
    return () => { active = false; controller.abort(); };
  }, [selectedId]);

  const mutate: Mutate = useCallback(async (label, operation) => {
    setBusy('Сохраняем изменения…'); setError(''); setNotice('');
    try {
      const stream = await operation();
      setCampaign((current) => current ? { ...current, streams: current.streams.map((item) => item.id === stream.id ? stream : item) } : null);
      setNotice(label); return true;
    } catch (reason) { setError(messageOf(reason)); return false; }
    finally { setBusy(''); }
  }, []);

  function select(id: number) {
    setCreating(false);
    if (selectedId !== id) window.location.hash = new URLSearchParams({ campaign: String(id) }).toString();
  }

  async function sync() {
    if (!campaign) return;
    setBusy('Синхронизируем с Keitaro…'); setError(''); setNotice('');
    try {
      setCampaign(await send<Campaign>(`/campaigns/${campaign.id}/fetch`, { discard_draft: false }));
      setNotice('Потоки обновлены. Удалённые в Keitaro офферы сохранены в архиве');
    } catch (reason) { setError(messageOf(reason)); }
    finally { setBusy(''); }
  }

  const dirtyCount = campaign?.streams.filter((stream) => stream.dirty).length ?? 0;
  const trackingUrl = safeTrackingUrl(campaign?.tracking_url ?? null);

  return <div className="app-shell">
    <a className="skip-link" href="#workspace">Перейти к редактору</a>
    <header className="app-header"><a className="wordmark" href="/">Adrobot</a><span className="header-purpose">Кампании Keitaro</span><span className="connection"><span className={catalogs ? 'connection-dot' : 'connection-dot is-offline'} />{catalogs ? 'API подключён' : 'Подключение к API'}</span></header>
    <div className="app-layout">
      <aside className="campaign-sidebar" aria-label="Список кампаний">
        <div className="sidebar-heading"><h2>Кампании</h2><span className="mono">{campaigns.length}</span></div>
        <button className="button button-primary create-button" disabled={!catalogs || Boolean(busy)} onClick={() => { setCreating(true); setError(''); setNotice(''); }}>Создать кампанию</button>
        <div className="field campaign-search"><label htmlFor="campaign-search">Поиск кампании</label><input type="search" id="campaign-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Название или ID" /></div>
        <div className="campaign-list" aria-busy={listLoading}>
          {listLoading ? <div className="list-loading" role="status"><span /><span /><span /><p>Загружаем кампании…</p></div> : campaigns.length ? campaigns.map((item) => <button className={`campaign-item${selectedId === item.id && !creating ? ' is-selected' : ''}`} key={item.id} disabled={Boolean(busy)} aria-current={selectedId === item.id && !creating ? 'true' : undefined} onClick={() => select(item.id)}><span>{item.name}</span><span className="mono">#{item.id}</span></button>) : <p className="list-empty">{search ? 'По этому запросу кампаний нет' : 'Кампаний пока нет. Создайте первую — она появится здесь'}</p>}
        </div>
        <button className="text-button refresh-list" disabled={Boolean(busy) || listLoading} onClick={() => { setError(''); setRetry((value) => value + 1); }}>Обновить список</button>
        <p className="sidebar-note">Работаем с кампаниями<br /><span className="mono">{catalogs?.campaign_prefix ?? 'test_borkoev_'}</span></p>
      </aside>
      <main id="workspace" className="workspace" tabIndex={-1}>
        <div className="feedback" aria-live="polite">{notice ? <p className="notice notice-success">{notice}</p> : null}</div>
        {error ? <div className="notice notice-error" role="alert"><p>{error}</p><button className="text-button" onClick={() => { setError(''); setRetry((value) => value + 1); }}>Обновить список и подключение</button></div> : null}
        {busy ? <p className="busy-message" role="status">{busy}</p> : null}
        {creating && catalogs ? <CreateCampaign catalogs={catalogs} onBusyChange={(value) => setBusy(value ? 'Создаём кампанию…' : '')} onCancel={() => setCreating(false)} onCreated={(result) => {
          setCreating(false); setCampaigns((items) => [result, ...items.filter((item) => item.id !== result.id)]);
          setSearch(''); setNotice('Кампания создана в Keitaro');
          if (selectedId !== result.id) select(result.id); else setCampaign(result);
        }} /> : campaign ? <>
          <header className="workspace-heading"><div><h1>{campaign.name}</h1><div className="campaign-meta"><span className="mono">Кампания #{campaign.id}</span>{trackingUrl ? <a href={trackingUrl} target="_blank" rel="noopener noreferrer">Открыть ссылку кампании</a> : null}</div></div><button className="button sync-button" disabled={Boolean(busy) || dirtyCount > 0} title={dirtyCount > 0 ? 'Сначала опубликуйте или отмените черновики' : 'Получить актуальные потоки из Keitaro'} onClick={() => void sync()}>Синхронизировать</button></header>
          <div className="editor-intro"><p>Распределение трафика</p><span>{dirtyCount ? `Неопубликованных потоков: ${dirtyCount}` : 'Все изменения опубликованы'}</span></div>
          {campaign.streams.length ? <div className="streams">{campaign.streams.map((stream) => <StreamPanel key={stream.id} stream={stream} busy={Boolean(busy)} mutate={mutate} />)}</div> : <div className="empty-workspace"><h2>У кампании пока нет потоков</h2><p>Добавьте потоки в Keitaro и синхронизируйте кампанию</p></div>}
        </> : !busy && !creating ? <section className="empty-workspace"><div className="empty-routes" aria-hidden="true"><span /><span /><span /></div><h1>Выберите кампанию</h1><p>Здесь будут потоки, офферы и их доли трафика. Черновики сохраняются между посещениями</p>{catalogs ? <button className="button" onClick={() => setCreating(true)}>Создать новую</button> : null}</section> : null}
      </main>
    </div>
  </div>;
}
