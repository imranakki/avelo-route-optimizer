"use client";

import { useEffect, useId, useRef, useState } from "react";
import { api, type Place } from "@/lib/api";

// A resolved endpoint: either a searched place or a pin dropped on the map.
export type Endpoint = { lat: number; lon: number; name: string; label: string };

type Props = {
  role: "origin" | "destination";
  value: Endpoint | null;
  onChange: (value: Endpoint | null) => void;
  onLocate?: () => void; // "use my location" (origin only)
  locating?: boolean;
};

const KIND_ICON: Record<string, string> = {
  university: "🎓",
  college: "🎓",
  school: "🏫",
  hospital: "🏥",
  station: "🚉",
  bus_stop: "🚏",
  park: "🌳",
  museum: "🏛",
  restaurant: "🍽",
  cafe: "☕",
  library: "📚",
  mall: "🛍",
  supermarket: "🛒",
  stadium: "🏟",
  attraction: "📍",
};

export default function PlaceSearch({ role, value, onChange, onLocate, locating }: Props) {
  const [query, setQuery] = useState(value ? value.name : "");
  const [prevValue, setPrevValue] = useState(value);
  const [results, setResults] = useState<Place[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const listId = useId();

  // Reflect an externally-set value (map click, geolocation) in the input. Done
  // during render, as React recommends for state derived from props.
  if (value !== prevValue) {
    setPrevValue(value);
    setQuery(value ? value.name : "");
  }

  // Debounced search; cancels the in-flight request when the query changes.
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
      setLoading(true);
      try {
        setResults(await api.geocode(q, ctrl.signal));
        setActive(-1);
      } catch (err) {
        if (!(err instanceof DOMException && err.name === "AbortError")) setResults([]);
      } finally {
        if (abortRef.current === ctrl) setLoading(false);
      }
    }, idle ? 0 : 220);
    return () => clearTimeout(timer);
  }, [query, open, value]);

  const pick = (p: Place) => {
    onChange({ lat: p.lat, lon: p.lon, name: p.name, label: p.label });
    setOpen(false);
    setResults([]);
    inputRef.current?.blur();
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open || results.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (a + 1) % results.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (a - 1 + results.length) % results.length);
    } else if (e.key === "Enter" && active >= 0) {
      e.preventDefault();
      pick(results[active]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  const dot = role === "origin" ? "bg-accent" : "bg-danger";
  const placeholder = role === "origin" ? "Starting point — search or click the map" : "Destination — search or click the map";

  return (
    <div className="relative">
      <label className="mb-1 block text-[11px] font-medium uppercase tracking-[0.06em] text-muted">
        {role === "origin" ? "From" : "To"}
      </label>
      <div className="flex items-center gap-2 rounded-lg border border-line bg-panel px-3 focus-within:border-accent">
        <span aria-hidden className={`h-2.5 w-2.5 shrink-0 rounded-full ${dot}`} />
        <input
          ref={inputRef}
          value={query}
          placeholder={placeholder}
          role="combobox"
          aria-expanded={open && results.length > 0}
          aria-controls={listId}
          aria-autocomplete="list"
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            if (value && e.target.value !== value.name) onChange(null);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onKeyDown={onKeyDown}
          className="min-w-0 flex-1 bg-transparent py-2.5 text-[15px] outline-none placeholder:text-muted/70"
        />
        {loading && <span className="text-xs text-muted">…</span>}
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
            className="text-muted hover:text-ink"
          >
            ×
          </button>
        )}
        {onLocate && (
          <button
            type="button"
            title="Use my location"
            aria-label="Use my location"
            disabled={locating}
            onMouseDown={(e) => e.preventDefault()}
            onClick={onLocate}
            className="text-muted hover:text-accent disabled:opacity-50"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3" />
              <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
              <circle cx="12" cy="12" r="8" />
            </svg>
          </button>
        )}
      </div>
      {value?.label && <p className="mt-1 truncate pl-5 text-xs text-muted">{value.label}</p>}

      {open && results.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          className="absolute left-0 right-0 z-30 mt-1 max-h-72 overflow-auto rounded-lg border border-line bg-panel py-1 shadow-lg"
        >
          {results.map((p, i) => (
            <li
              key={`${p.lat},${p.lon},${p.name}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => e.preventDefault()}
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(p)}
              className={`flex cursor-pointer items-start gap-2 px-3 py-2 ${i === active ? "bg-panel-2" : ""}`}
            >
              <span className="w-5 text-center text-sm" aria-hidden>
                {KIND_ICON[p.kind] ?? "📍"}
              </span>
              <span className="min-w-0">
                <span className="block truncate text-[14px]">{p.name}</span>
                {p.label && <span className="block truncate text-xs text-muted">{p.label}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
