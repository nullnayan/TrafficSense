// @file: frontend/src/FilterPill.tsx
// @exports: FilterPill
// @depends_on: React, hud.css
// @purpose: Accessible tactical filter menus without native select widgets.
import {useEffect, useRef, useState} from 'react';
export default function FilterPill({label, value, options, change}: {label: string; value: string;
  options: string[]; change: (value: string) => void}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (event: PointerEvent) => {if (!root.current?.contains(event.target as Node)) setOpen(false);};
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, []);
  return <div className="filter-pill" ref={root} onKeyDown={e => {if (e.key === 'Escape') setOpen(false);}}>
    <button aria-label={`${label} filter`} aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen(!open)}>
      {value === 'all' ? label : value} <span>⌄</span></button>
    {open && <div className="filter-menu glass" role="listbox" aria-label={label}>{['all', ...options].map(option =>
      <button role="option" aria-selected={value === option} key={option} onClick={() => {change(option); setOpen(false);}}>
        {option === 'all' ? `All ${label.toLowerCase()}` : option}</button>)}</div>}
  </div>;
}
