"use client";

import { useEffect, useId, useRef, useState } from "react";
import { suggest, type Suggestion } from "@/lib/places";

// A resolved endpoint: a searched place, a station, or a pin dropped on the map.
export type Endpoint = { lat: number; lon: number; name: string; label: string };

type Props = {
  role: "origin" | "via" | "destination";
  index?: number;
  value: Endpoint | null;
  onChange: (value: Endpoint | null) => void;
  onLocate?: () => void;
  locating?: boolean;
  autoFocus?: boolean;
};

export default function PlaceSearch({ role, index, value, onChange, onLocate, locating, autoFocus }: Props) {
  const [query, setQuery] = useState(value ? value.name : "");
  const [prevValue, setPrevValue] = useState(value);
  const [results, setResults] = useState<Suggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const listId = useId();

  // Reflect an externally-set value (map click, station, geolocation) in the input.
  if (value !== prevValue) {
    setPrevValue(value);
    setQuery(value ? value.name : "");
  }

  useEffect(() => {
    if (!open) return;
    const q = query.trim();
    const idle = q.length < 2 || (value !== null && q === value.name);
    const timer = setTimeout(async () => {
      if (idle) {
        setResults([]);
        return;
      }
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setBusy(true);
      try {
        const out = await suggest(q, ctrl.signal);
        if (!ctrl.signal.aborted) {
          setResults(out);
          setActive(-1);
        }
      } catch (err) {
        if (!(err instanceof DOMException && err.name === "AbortError")) setResults([]);
      } finally {
        if (abortRef.current === ctrl) setBusy(false);
      }
    }, idle ? 0 : 200);
    return () => clearTimeout(timer);
  }, [query, open, value]);

  const pick = async (s: Suggestion) => {
    setOpen(false);
    setResults([]);
    inputRef.current?.blur();
    const coord = s.coord ?? (s.resolve ? await s.resolve() : null);
    if (coord) onChange({ ...coord, name: s.name, label: s.label });
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open || results.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (a + 1) % results.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (a - 1 + results.length) % results.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      void pick(results[active >= 0 ? active : 0]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className="relative">
      <div className="flex items-center gap-3 border-b border-rule py-2 focus-within:border-ink">
        <span className="label w-10 shrink-0">{role === "origin" ? "From" : role === "destination" ? "To" : `Via ${index ?? ""}`}</span>
        <input
          ref={inputRef}
          value={query}
          placeholder={role === "origin" ? "Search a place, or tap the map" : role === "via" ? "A place to visit on the way" : "Where to?"}
          role="combobox"
          aria-expanded={open && results.length > 0}
          aria-controls={listId}
          aria-autocomplete="list"
          autoComplete="off"
          autoFocus={autoFocus}
          spellCheck={false}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            if (value && e.target.value !== value.name) onChange(null);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={onKeyDown}
          className="min-w-0 flex-1 bg-transparent py-1 text-[16px] text-ink outline-none placeholder:text-muted"
        />
        {busy && <span className="num text-[11px] text-muted">…</span>}
        {value && (
          <button
            type="button"
            aria-label="Clear"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              onChange(null);
              setQuery("");
              inputRef.current?.focus();
            }}
            className="grid h-8 w-8 place-items-center text-muted hover:text-ink"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M2 2l10 10M12 2L2 12" />
            </svg>
          </button>
        )}
        {onLocate && !value && (
          <button
            type="button"
            title="Use my location"
            aria-label="Use my location"
            disabled={locating}
            onMouseDown={(e) => e.preventDefault()}
            onClick={onLocate}
            className="grid h-8 w-8 place-items-center text-muted hover:text-ink disabled:opacity-40"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
              <circle cx="12" cy="12" r="3" />
              <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
              <circle cx="12" cy="12" r="8" />
            </svg>
          </button>
        )}
      </div>
      {value?.label && <p className="mt-1 truncate pl-[52px] text-[12px] text-muted">{value.label}</p>}

      {open && results.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          className="absolute left-0 right-0 z-30 mt-1 max-h-80 overflow-auto border border-rule-strong bg-paper shadow-[0_12px_32px_-12px_rgb(0_0_0/0.35)]"
        >
          {results.map((s, i) => (
            <li
              key={s.id}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => e.preventDefault()}
              onMouseEnter={() => setActive(i)}
              onClick={() => void pick(s)}
              className={`cursor-pointer border-b border-rule px-3 py-2 last:border-b-0 ${i === active ? "bg-paper-2" : ""}`}
            >
              <span className="block truncate text-[14px] text-ink">{s.name}</span>
              {s.label && <span className="block truncate text-[12px] text-muted">{s.label}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
