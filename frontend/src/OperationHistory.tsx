import { useCallback, useEffect, useState } from 'react';
import { api, send } from './api';
import type { Operation } from './api';
import { ErrorNotice, operationDescription } from './ErrorNotice';

const statuses: Record<Operation['status'], string> = {
  pending: 'В работе', uncertain: 'Нужно проверить', succeeded: 'Выполнено', failed: 'Не выполнено', conflict: 'Конфликт',
};

export function OperationHistory({ selectedOperation, onOpenCampaign }: { selectedOperation: string | null; onOpenCampaign: (id: number) => void }) {
  const [items, setItems] = useState<Operation[]>([]);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const result = await api<{ items: Operation[] }>('/operations?limit=30', { signal });
      if (selectedOperation && !result.items.some((item) => item.id === selectedOperation)) result.items.unshift(await api<Operation>(`/operations/${selectedOperation}`, { signal }));
      if (!signal?.aborted) { setItems(result.items); setError(null); }
    } catch (reason) { if (!signal?.aborted) setError(reason); }
    finally { if (!signal?.aborted) setLoading(false); }
  }, [selectedOperation]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  async function reconcile(operation: Operation) {
    if (checking) return;
    setChecking(operation.id); setError(null);
    try {
      const result = await send<Operation>(`/operations/${operation.id}/reconcile`, {});
      setItems((current) => current.map((item) => item.id === result.id ? result : item));
    } catch (reason) { setError(reason); }
    finally { setChecking(null); }
  }

  return <section className="operation-history">
    <header className="workspace-heading"><div><h1>Журнал операций</h1><p>Создание и публикация с подтверждённым результатом</p></div><button className="button" disabled={loading || Boolean(checking)} onClick={() => void load()}>Обновить журнал</button></header>
    {error ? <ErrorNotice error={error} onRefresh={() => void load()} /> : null}
    {loading ? <p className="busy-message" role="status">Загружаем операции…</p> : null}
    {!loading && !items.length ? <div className="empty-workspace"><h2>Пока нет операций</h2><p>После создания кампании или публикации здесь появится её результат</p></div> : null}
    <div className="operation-list">{items.map((operation) => <article className={`operation-item${selectedOperation === operation.id ? ' is-highlighted' : ''}`} key={operation.id}>
      <div className="operation-item-heading"><h2>{operation.kind === 'create_campaign' ? 'Создание кампании' : 'Публикация потока'}</h2><span className={`operation-status operation-${operation.status}`}>{statuses[operation.status]}</span></div>
      <p className="operation-context">{operation.campaign_id ? `Кампания #${operation.campaign_id}` : 'Новая кампания'}{operation.stream_id ? ` · поток #${operation.stream_id}` : ''}<time dateTime={operation.updated_at}>{new Date(operation.updated_at).toLocaleString('ru-RU')}</time></p>
      {operationDescription(operation.error_code, operation.detail) ? <p className="operation-detail">{operationDescription(operation.error_code, operation.detail)}</p> : null}
      {['pending', 'uncertain'].includes(operation.status) ? <p className="operation-help">Проверка прочитает состояние Keitaro. Повторной записи не будет</p> : null}
      <div className="operation-actions">{['pending', 'uncertain'].includes(operation.status) ? <button className="button" disabled={Boolean(checking)} onClick={() => void reconcile(operation)}>{checking === operation.id ? 'Проверяем…' : 'Проверить результат'}</button> : null}{operation.campaign_id ? <button className="text-button" disabled={Boolean(checking)} onClick={() => onOpenCampaign(operation.campaign_id!)}>Открыть кампанию</button> : null}</div>
      <span className="operation-reference mono">{operation.id}</span>
    </article>)}</div>
  </section>;
}
