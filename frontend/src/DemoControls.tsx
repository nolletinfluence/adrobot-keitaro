import { useState } from 'react';
import { send } from './api';
import { ErrorNotice } from './ErrorNotice';

export function DemoControls({ disabled }: { disabled: boolean }) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function arm() {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      await send('/demo/fault', { kind: 'publish_timeout' });
      setArmed(true);
    } catch (reason) { setError(reason); }
    finally { setBusy(false); }
  }

  return <details className="demo-controls">
    <summary>Демо: проверить восстановление после сбоя</summary>
    <p>Измените доли или состав офферов, включите сценарий и опубликуйте. Демо применит изменение и потеряет ответ. Затем нажмите «Проверить результат»: публикация подтвердится без повторной записи</p>
    <button className="button" disabled={disabled || busy} onClick={() => void arm()}>{busy ? 'Включаем сценарий…' : 'Потерять следующий ответ публикации'}</button>
    {armed ? <p role="status">Подготовлен разовый сбой ответа публикации в демо</p> : null}
    {error ? <ErrorNotice error={error} /> : null}
  </details>;
}
