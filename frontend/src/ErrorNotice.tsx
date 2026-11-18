import { ApiError, messageOf } from './api';

const recoveryText: Record<string, string> = {
  VALIDATION_ERROR: 'Проверьте параметры запроса',
  STALE_VERSION: 'Поток изменился в другой вкладке. Обновите его перед продолжением',
  REMOTE_CONFLICT: 'В Keitaro уже другие настройки. Черновик сохранён — сначала сравните изменения',
  IDEMPOTENCY_CONFLICT: 'Эта попытка уже связана с другими параметрами',
  OPERATION_IN_PROGRESS: 'Операция ещё выполняется. Её состояние доступно в журнале',
  OPERATION_UNCERTAIN: 'Ответ Keitaro не получен. Сначала проверим результат операции',
  OPERATION_PENDING: 'Поток связан с незавершённой публикацией. Сначала проверьте её результат',
  OPERATION_RECONCILIATION_REQUIRED: 'Эта попытка уже отправлена. Сначала проверим её результат',
  IDEMPOTENCY_KEY_CONFLICT: 'Эта попытка уже связана с другими параметрами',
  INVALID_ALLOCATION: 'Так распределить трафик нельзя. Сумма активных долей должна быть 100%',
  DRAFT_CONFLICT: 'В кампании есть черновик. Опубликуйте или отмените его перед синхронизацией',
  LOCAL_STATE_CONFLICT: 'Локальное состояние изменилось. Обновите кампанию перед продолжением',
  LOCAL_PERSISTENCE_ERROR: 'Не удалось подтвердить результат в локальной базе. Проверьте операцию в журнале',
  OFFER_ALREADY_ACTIVE: 'Этот оффер уже участвует в ротации',
  OFFER_UNAVAILABLE: 'Оффер недоступен в Keitaro',
  OFFER_REMOVED: 'Сначала верните оффер из архива',
  INVALID_CATALOG_REFERENCE: 'Домен, группа или источник больше недоступны. Обновите список',
  STREAM_NOT_EDITABLE: 'Этот поток доступен только для просмотра',
  INVALID_CAMPAIGN_PREFIX: 'Используйте тестовый префикс из настроек приложения',
  NOT_FOUND: 'Данные не найдены. Обновите список кампаний',
  UPSTREAM_NOT_CONFIGURED: 'Укажите адрес и API-ключ Keitaro на сервере или включите демо-режим',
  UPSTREAM_AUTH: 'Keitaro отклонил доступ. Проверьте серверный API-ключ и права учётной записи',
  UPSTREAM_DEADLINE: 'Операция заняла больше отведённого времени',
  UPSTREAM_NOT_FOUND: 'Объект больше не найден в Keitaro',
  UPSTREAM_INVALID_RESPONSE: 'Ответ Keitaro не удалось подтвердить. Проверьте результат операции',
  UPSTREAM_TIMEOUT: 'Keitaro не ответил вовремя',
  UPSTREAM_UNAVAILABLE: 'Не удалось связаться с Keitaro',
  UPSTREAM_REJECTED: 'Keitaro отклонил запрос. Проверьте параметры и доступы',
  PUBLICATION_UNCONFIRMED: 'Keitaro ещё не подтвердил ожидаемое распределение',
  CREATION_UNCONFIRMED: 'Создание кампании пока не подтверждено',
  CREATION_INCOMPLETE: 'Кампания создана частично. Проверьте её потоки в Keitaro',
  CREATION_ARCHIVED: 'Созданная кампания находится в архиве Keitaro',
  CREATION_ROLLBACK_UNCERTAIN: 'Не удалось подтвердить отмену частичного создания. Проверьте Keitaro',
  OPERATION_TARGET_MISMATCH: 'Операция относится к другому трекеру. Верните его настройки перед проверкой',
  DATABASE_TARGET_MISMATCH: 'Эта база привязана к другому трекеру. Используйте отдельную базу для нового подключения',
};

export function operationDescription(code: string | null, detail: string | null): string | null {
  return code ? recoveryText[code] ?? detail : detail;
}

export function ErrorNotice({ error, onInspect, onRefresh }: {
  error: unknown;
  onInspect?: (operationId: string) => void;
  onRefresh?: () => void;
}) {
  const apiError = error instanceof ApiError ? error : null;
  const headline = apiError ? recoveryText[apiError.code] ?? messageOf(error) : messageOf(error);
  return <div className="notice notice-error" role="alert">
    <p>{headline}</p>
    {apiError?.outcome === 'unknown' ? <p>Результат пока не подтверждён. Новая запись в Keitaro автоматически не отправляется</p> : null}
    <div className="notice-actions">
      {apiError?.operationId && onInspect ? <button className="text-button" type="button" onClick={() => onInspect(apiError.operationId!)}>Открыть операцию</button> : null}
      {onRefresh ? <button className="text-button" type="button" onClick={onRefresh}>Обновить данные</button> : null}
    </div>
    {apiError?.requestId ? <span className="request-reference mono">Номер обращения: {apiError.requestId}</span> : null}
  </div>;
}
