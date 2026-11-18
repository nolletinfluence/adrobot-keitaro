import { useEffect, useId, useRef, useState } from 'react';
import { api, messageOf } from './api';
import type { Option } from './api';

type Props = {
  label: string;
  value: Option | null;
  onChange: (offer: Option | null) => void;
  disabled?: boolean;
  excludeIds?: number[];
};

export function OfferPicker({ label, value, onChange, disabled = false, excludeIds }: Props) {
  const id = useId();
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<Option[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [active, setActive] = useState(-1);
  const sequence = useRef(0);
  const container = useRef<HTMLDivElement>(null);
  const visible = items.filter((item) => !excludeIds?.includes(item.id));

  useEffect(() => {
    if (!open || value || disabled) return;
    const controller = new AbortController();
    const current = ++sequence.current;
    setLoading(true);
    setError('');
    setItems([]);
    const timer = window.setTimeout(() => {
      api<{ items: Option[]; has_more: boolean }>(`/offers?q=${encodeURIComponent(query)}&limit=20`, { signal: controller.signal })
        .then((result) => {
          if (current === sequence.current) { setItems(result.items); setActive(-1); }
        })
        .catch((reason: unknown) => {
          if (!controller.signal.aborted && current === sequence.current) setError(messageOf(reason));
        })
        .finally(() => {
          if (!controller.signal.aborted && current === sequence.current) setLoading(false);
        });
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [open, query, value, disabled]);

  const choose = (item: Option) => { onChange(item); setOpen(false); setActive(-1); };

  return <div className="offer-picker" ref={container} onBlur={(event) => {
    if (!container.current?.contains(event.relatedTarget)) setOpen(false);
  }}>
    <label htmlFor={id}>{label}</label>
    <input
      id={id}
      type="text"
      role="combobox"
      autoComplete="off"
      aria-autocomplete="list"
      aria-expanded={open && !value}
      aria-controls={`${id}-list`}
      aria-activedescendant={open && !value && active >= 0 && visible[active] ? `${id}-${visible[active].id}` : undefined}
      placeholder="ID или название оффера"
      value={value ? `#${value.id} · ${value.name}` : query}
      disabled={disabled}
      onFocus={() => { if (!value) setOpen(true); }}
      onChange={(event) => { onChange(null); setQuery(event.target.value); setOpen(true); setActive(-1); }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') { setOpen(false); setActive(-1); }
        if (event.key === 'ArrowDown') {
          event.preventDefault(); setOpen(true); setActive((index) => Math.min(index + 1, visible.length - 1));
        }
        if (event.key === 'ArrowUp') { event.preventDefault(); setActive((index) => Math.max(0, index - 1)); }
        if (event.key === 'Enter' && open && active >= 0 && visible[active]) { event.preventDefault(); choose(visible[active]); }
      }}
    />
    {value ? <button className="picker-clear" type="button" disabled={disabled} onClick={() => { onChange(null); setQuery(''); setOpen(true); }}>Сменить</button> : null}
    {open && !value ? <div className="offer-results">
      <ul id={`${id}-list`} role="listbox" aria-label={label} aria-busy={loading}>
        {visible.map((item, index) => <li key={item.id} id={`${id}-${item.id}`} role="option" aria-selected={active === index} onMouseDown={(event) => event.preventDefault()} onClick={() => choose(item)}>
          <span className="mono">#{item.id}</span><span>{item.name}</span>
        </li>)}
      </ul>
      {loading ? <p role="status">Ищем офферы…</p> : null}
      {!loading && error ? <p role="alert" className="error-text">{error}</p> : null}
      {!loading && !error && visible.length === 0 ? <p>Подходящих офферов нет</p> : null}
    </div> : null}
  </div>;
}
