import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api, send } from './api';
import type { Campaign, Catalogs, Option } from './api';
import { CreateCampaign } from './CreateCampaign';
import { DemoControls } from './DemoControls';
import { ErrorNotice } from './ErrorNotice';
import { OperationHistory } from './OperationHistory';
import { StreamPanel } from './StreamPanel';
import type { Mutate } from './StreamPanel';

function readLocation() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const id = Number(params.get('campaign'));
  return {
    campaignId: Number.isSafeInteger(id) && id > 0 ? id : null,
    history: params.get('view') === 'operations',
    operationId: params.get('operation'),
  };
}

function safeTrackingUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

export default function App() {
  const [route, setRoute] = useState(readLocation);
  const [catalogs, setCatalogs] = useState<Catalogs | null>(null);
  const [campaigns, setCampaigns] = useState<Option[]>([]);
  const [search, setSearch] = useState('');
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState('');
  const [formBusy, setFormBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [listLoading, setListLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState('');
  const [retry, setRetry] = useState(0);
  const mutationInFlight = useRef(false);
  const selection = useRef(route.campaignId);
  selection.current = route.campaignId;

  useEffect(() => {
    const controller = new AbortController();
    api<Catalogs>('/catalogs', { signal: controller.signal }).then(setCatalogs).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason);
    });
    return () => controller.abort();
  }, [retry]);

  useEffect(() => {
    const controller = new AbortController();
    setListLoading(true);
    const timer = window.setTimeout(() => {
      api<{ items: Option[] }>(`/campaigns?q=${encodeURIComponent(search)}`, { signal: controller.signal })
        .then((result) => { if (!controller.signal.aborted) setCampaigns(result.items); })
        .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason); })
        .finally(() => { if (!controller.signal.aborted) setListLoading(false); });
    }, 200);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [search, retry]);

  useEffect(() => {
    function changed() { setRoute(readLocation()); setCreating(false); setError(null); setNotice(''); }
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, []);

  useEffect(() => {
    if (!route.campaignId) { setCampaign(null); setLoading(false); return; }
    const controller = new AbortController();
    const campaignId = route.campaignId;
    setLoading(true); setError(null); setNotice(''); setCampaign(null);
    async function load() {
      try {
        let result: Campaign;
        try { result = await api<Campaign>(`/campaigns/${campaignId}`, { signal: controller.signal }); }
        catch (reason) {
          if (!(reason instanceof ApiError) || reason.status !== 404) throw reason;
          result = await api<Campaign>(`/campaigns/${campaignId}/fetch`, {
            method: 'POST', body: JSON.stringify({ discard_draft: false }), signal: controller.signal,
          });
        }
        if (!controller.signal.aborted) setCampaign(result);
      } catch (reason) { if (!controller.signal.aborted) setError(reason); }
      finally { if (!controller.signal.aborted) setLoading(false); }
    }
    void load();
    return () => controller.abort();
  }, [route.campaignId, retry]);

  useEffect(() => {
    if (catalogs?.mode === 'demo' && !route.campaignId && !route.history && !creating && campaigns.length) {
      window.location.hash = new URLSearchParams({ campaign: String(campaigns[0].id) }).toString();
    }
  }, [catalogs?.mode, route.campaignId, route.history, creating, campaigns]);

  const mutate: Mutate = useCallback(async (label, operation) => {
    if (mutationInFlight.current) return false;
    mutationInFlight.current = true;
    const campaignId = selection.current;
    setBusy('Сохраняем изменения…'); setError(null); setNotice('');
    try {
      const stream = await operation();
      if (selection.current === campaignId) {
        setCampaign((current) => current ? {
          ...current, streams: current.streams.map((item) => item.id === stream.id ? stream : item),
        } : null);
        setNotice(label);
      }
      return true;
    } catch (reason) {
      if (selection.current === campaignId) {
        setError(reason);
        if (campaignId) {
          try {
            const latest = await api<Campaign>(`/campaigns/${campaignId}`);
            if (selection.current === campaignId) setCampaign(latest);
          } catch {}
        }
      }
      return false;
    } finally { mutationInFlight.current = false; setBusy(''); }
  }, []);

  function select(id: number) {
    setCreating(false);
    if (route.campaignId === id && !route.history) setRetry((value) => value + 1);
    else window.location.hash = new URLSearchParams({ campaign: String(id) }).toString();
  }

  function inspectOperation(id?: string) {
    setCreating(false);
    window.location.hash = new URLSearchParams({ view: 'operations', ...(id ? { operation: id } : {}) }).toString();
  }

  async function sync() {
    if (!campaign || mutationInFlight.current) return;
    mutationInFlight.current = true;
    const campaignId = campaign.id;
    setBusy('Синхронизируем с Keitaro…'); setError(null); setNotice('');
    try {
      const updated = await send<Campaign>(`/campaigns/${campaignId}/fetch`, { discard_draft: false });
      if (selection.current === campaignId) {
        setCampaign(updated);
        setNotice('Потоки обновлены. Удалённые в Keitaro офферы сохранены в архиве');
      }
    } catch (reason) { if (selection.current === campaignId) setError(reason); }
    finally { mutationInFlight.current = false; setBusy(''); }
  }

  function created(result: Campaign) {
    setCreating(false); setSearch(''); setCampaigns((items) => [result, ...items.filter((item) => item.id !== result.id)]);
    select(result.id);
  }

  const disabled = Boolean(busy) || formBusy || loading;
  const dirtyCount = campaign?.streams.filter((stream) => stream.dirty).length ?? 0;
  const pendingCount = campaign?.streams.filter((stream) => stream.operation).length ?? 0;
  const trackingUrl = safeTrackingUrl(campaign?.tracking_url ?? null);

  return <div className="app-shell">
    <a className="skip-link" href="#workspace">Перейти к редактору</a>
    <header className="app-header">
      <a className="wordmark" href="/">Adrobot</a><span className="header-purpose">Кампании Keitaro</span>
      <span className="connection"><span className={catalogs ? 'connection-dot' : 'connection-dot is-offline'} />
        {catalogs?.mode === 'demo' ? 'Демо · локальные данные' : catalogs ? 'Keitaro подключён' : 'Подключение к API'}
      </span>
    </header>
    <div className="app-layout">
      <aside className="campaign-sidebar" aria-label="Список кампаний">
        <div className="sidebar-heading"><h2>Кампании</h2><span className="mono">{campaigns.length}</span></div>
        <button className="button button-primary create-button" disabled={!catalogs || disabled} onClick={() => { setCreating(true); setError(null); setNotice(''); }}>Создать кампанию</button>
        <button className={`sidebar-history${route.history && !creating ? ' is-selected' : ''}`} aria-pressed={route.history && !creating} disabled={disabled} onClick={() => inspectOperation()}>Журнал операций</button>
        <div className="field campaign-search"><label htmlFor="campaign-search">Поиск кампании</label><input type="search" id="campaign-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Название или ID" /></div>
        <div className="campaign-list" aria-busy={listLoading}>
          {listLoading ? <div className="list-loading" role="status"><span /><span /><span /><p>Загружаем кампании…</p></div> : campaigns.length ? campaigns.map((item) => <button className={`campaign-item${route.campaignId === item.id && !creating && !route.history ? ' is-selected' : ''}`} key={item.id} disabled={disabled} aria-current={route.campaignId === item.id && !creating ? 'true' : undefined} onClick={() => select(item.id)}><span>{item.name}</span><span className="mono">#{item.id}</span></button>) : <p className="list-empty">{search ? 'По этому запросу кампаний нет' : 'Кампаний пока нет. Создайте первую — она появится здесь'}</p>}
        </div>
        <button className="text-button refresh-list" disabled={disabled || listLoading} onClick={() => setRetry((value) => value + 1)}>Обновить список</button>
        <p className="sidebar-note">{catalogs?.mode === 'demo' ? 'Демо-данные сохраняются локально' : 'Работаем с кампаниями'}<br /><span className="mono">{catalogs?.campaign_prefix ?? 'test_borkoev_'}</span></p>
      </aside>
      <main id="workspace" className="workspace" tabIndex={-1}>
        {catalogs?.mode === 'demo' ? <DemoControls disabled={disabled} /> : null}
        <div className="feedback" aria-live="polite">{notice ? <p className="notice notice-success">{notice}</p> : null}</div>
        {error ? <ErrorNotice error={error} onInspect={inspectOperation} onRefresh={() => setRetry((value) => value + 1)} /> : null}
        {busy || loading ? <p className="busy-message" role="status">{busy || 'Загружаем кампанию…'}</p> : null}
        {creating && catalogs ? <CreateCampaign key={`${catalogs.mode}-${catalogs.campaign_prefix}`} catalogs={catalogs} onBusyChange={setFormBusy} onInspectOperation={inspectOperation} onCancel={() => setCreating(false)} onCreated={created} /> : route.history ? <OperationHistory selectedOperation={route.operationId} onOpenCampaign={select} /> : campaign ? <>
          <header className="workspace-heading"><div><h1>{campaign.name}</h1><div className="campaign-meta"><span className="mono">Кампания #{campaign.id}</span>{catalogs?.mode === 'demo' ? <span>Демонстрационные данные</span> : trackingUrl ? <a href={trackingUrl} target="_blank" rel="noopener noreferrer">Открыть ссылку кампании</a> : null}</div></div><button className="button sync-button" disabled={disabled || dirtyCount > 0 || pendingCount > 0} title={pendingCount ? 'Сначала проверьте результат публикации' : dirtyCount ? 'Сначала опубликуйте или отмените черновики' : 'Получить актуальные потоки из Keitaro'} onClick={() => void sync()}>Синхронизировать</button></header>
          <div className="editor-intro"><p>Распределение трафика</p><span>{pendingCount ? `Ожидают подтверждения: ${pendingCount}` : dirtyCount ? `Неопубликованных потоков: ${dirtyCount}` : 'Все изменения опубликованы'}</span></div>
          {campaign.streams.length ? <div className="streams">{campaign.streams.map((stream) => <StreamPanel key={stream.id} stream={stream} busy={disabled} mutate={mutate} onInspectOperation={inspectOperation} />)}</div> : <div className="empty-workspace"><h2>У кампании пока нет потоков</h2><p>Добавьте потоки в Keitaro и синхронизируйте кампанию</p></div>}
        </> : !loading && !creating ? <section className="empty-workspace"><h1>Выберите кампанию</h1><p>Здесь будут потоки, офферы и их доли трафика. Черновики сохраняются между посещениями</p>{catalogs ? <button className="button" onClick={() => setCreating(true)}>Создать новую</button> : null}</section> : null}
      </main>
    </div>
  </div>;
}
