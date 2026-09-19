"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, type CompareResponse, type Station, type Vehicle } from "@/lib/api";
import PlaceSearch, { type Endpoint } from "./PlaceSearch";
import ResultsPanel, { choicesFrom } from "./ResultsPanel";

// MapLibre touches `window` at import time, so the map is client-only.
const MapView = dynamic(() => import("./MapView"), {
  ssr: false,
  loading: () => <div className="h-full w-full animate-pulse bg-panel-2" aria-hidden />,
});

const VEHICLES: { value: Vehicle; label: string }[] = [
  { value: "EFIT", label: "EFIT — electric" },
  { value: "ICONIC", label: "ICONIC — mechanical" },
];

const fmt = (p: { lat: number; lon: number }) => `${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}`;

export default function TripPlanner() {
  const [origin, setOrigin] = useState<Endpoint | null>(null);
  const [destination, setDestination] = useState<Endpoint | null>(null);
  const [vehicle, setVehicle] = useState<Vehicle>("EFIT");
  const [stations, setStations] = useState<Station[]>([]);
  const [data, setData] = useState<CompareResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [planning, setPlanning] = useState(false);
  const [locating, setLocating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const planAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    api.stations().then(setStations).catch(() => setStations([]));
  }, []);

  const fleet = useMemo(() => {
    const total: Record<string, number> = {};
    for (const s of stations) for (const [k, v] of Object.entries(s.bikes_by_type)) total[k] = (total[k] ?? 0) + v;
    return total;
  }, [stations]);

  // A pin dropped or dragged on the map: label it with the nearest named place.
  const setPin = useCallback(async (role: "origin" | "destination", p: { lat: number; lon: number }) => {
    const setter = role === "origin" ? setOrigin : setDestination;
    setter({ ...p, name: "Pinned location", label: fmt(p) });
    try {
      const place = await api.reverse(p);
      if (place) setter({ ...p, name: place.name, label: place.label ? `${place.label} · ${fmt(p)}` : fmt(p) });
    } catch {
      // keep the coordinate label
    }
  }, []);

  const onMapClick = useCallback(
    (p: { lat: number; lon: number }) => {
      if (!origin) void setPin("origin", p);
      else if (!destination) void setPin("destination", p);
      else void setPin("destination", p);
    },
    [origin, destination, setPin],
  );

  const locate = () => {
    if (!navigator.geolocation) return setError("Geolocation is not available in this browser.");
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocating(false);
        void setPin("origin", { lat: pos.coords.latitude, lon: pos.coords.longitude });
      },
      () => {
        setLocating(false);
        setError("Could not get your location.");
      },
      { enableHighAccuracy: true, timeout: 8000 },
    );
  };

  const swap = () => {
    setOrigin(destination);
    setDestination(origin);
  };

  const reset = () => {
    planAbort.current?.abort();
    setOrigin(null);
    setDestination(null);
    setData(null);
    setSelected(null);
    setError(null);
  };

  const plan = useCallback(async () => {
    if (!origin || !destination) return;
    planAbort.current?.abort();
    const ctrl = new AbortController();
    planAbort.current = ctrl;
    setPlanning(true);
    setError(null);
    try {
      const res = await api.compare(origin, destination, vehicle, ctrl.signal);
      setData(res);
      const first = choicesFrom(res)[0];
      setSelected(first ? first.key : null);
      setPanelOpen(true);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setData(null);
      setSelected(null);
      setError(err instanceof ApiError ? err.message : "The routing service is unreachable.");
    } finally {
      if (planAbort.current === ctrl) setPlanning(false);
    }
  }, [origin, destination, vehicle]);

  // Re-plan automatically once both ends are set, and whenever they or the bike type
  // change. Deferred a tick so a drag that fires several updates plans once.
  useEffect(() => {
    const t = setTimeout(() => {
      if (origin && destination) void plan();
      else setData(null);
    }, 0);
    return () => clearTimeout(t);
  }, [origin, destination, vehicle, plan]);

  const choices = useMemo(() => (data ? choicesFrom(data) : []), [data]);
  const current = choices.find((c) => c.key === selected) ?? null;

  return (
    <div className="grid h-full grid-rows-[1fr_auto] md:grid-cols-[400px_1fr] md:grid-rows-1">
      <aside
        className={`order-2 flex max-h-[55vh] flex-col overflow-hidden border-t border-line bg-panel md:order-1 md:max-h-none md:border-r md:border-t-0 ${
          panelOpen ? "" : "max-h-14"
        }`}
      >
        <header className="flex items-center justify-between px-4 pt-4 pb-2">
          <div>
            <h1 className="text-[17px] font-semibold tracking-tight">àVélo Route Optimizer</h1>
            <p className="text-xs text-muted">Québec City · live stations · real street routing</p>
          </div>
          <button type="button" className="text-xs text-muted md:hidden" onClick={() => setPanelOpen((o) => !o)}>
            {panelOpen ? "Hide" : "Show"}
          </button>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-4 pb-4">
          <div className="space-y-3">
            <PlaceSearch role="origin" value={origin} onChange={setOrigin} onLocate={locate} locating={locating} />
            <div className="flex items-center justify-between">
              <PlaceSearchDivider />
              <button
                type="button"
                onClick={swap}
                disabled={!origin && !destination}
                title="Swap origin and destination"
                className="rounded-md border border-line px-2 py-0.5 text-xs text-muted hover:text-ink disabled:opacity-40"
              >
                ⇅ swap
              </button>
            </div>
            <PlaceSearch role="destination" value={destination} onChange={setDestination} />
          </div>

          <div className="flex items-end gap-2">
            <label className="flex-1">
              <span className="mb-1 block text-[11px] font-medium uppercase tracking-[0.06em] text-muted">Bike</span>
              <select
                value={vehicle}
                onChange={(e) => setVehicle(e.target.value as Vehicle)}
                className="w-full rounded-lg border border-line bg-panel px-3 py-2 text-[14px] outline-none focus:border-accent"
              >
                {VEHICLES.map((v) => (
                  <option key={v.value} value={v.value}>
                    {v.label}
                    {fleet[v.value] !== undefined ? ` (${fleet[v.value]} available)` : ""}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => void plan()}
              disabled={!origin || !destination || planning}
              className="rounded-lg bg-accent px-4 py-2 text-[14px] font-semibold text-accent-ink disabled:opacity-50"
            >
              {planning ? "Planning…" : "Plan trip"}
            </button>
            <button type="button" onClick={reset} className="rounded-lg border border-line px-3 py-2 text-[14px] text-muted hover:text-ink">
              Clear
            </button>
          </div>

          {!origin && !destination && !data && (
            <div className="rounded-lg bg-panel-2 p-3 text-[13px] leading-relaxed text-muted">
              Search for a place or click the map to set where you start, then where you are going. You keep one bike for the whole trip;
              a <b className="text-ink">reset</b> is docking and re-unlocking at a station so the 30-minute clock restarts.
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                <span><i className="inline-block h-2 w-2 rounded-full bg-hard" /> bikes &amp; docks</span>
                <span><i className="inline-block h-2 w-2 rounded-full bg-danger" /> no bikes</span>
                <span><i className="inline-block h-2 w-2 rounded-full bg-warn" /> no docks</span>
              </div>
            </div>
          )}

          {error && (
            <p role="alert" className="rounded-lg border border-danger/40 bg-danger/5 px-3 py-2 text-[13px] text-danger">
              {error}
            </p>
          )}

          {planning && !data && <p className="text-[13px] text-muted">Planning…</p>}

          {data && <ResultsPanel data={data} choices={choices} selectedKey={selected} onSelect={setSelected} />}
        </div>
      </aside>

      <main className="relative order-1 min-h-[45vh] md:order-2 md:min-h-0">
        <MapView
          stations={stations}
          origin={origin}
          destination={destination}
          itinerary={current?.itinerary ?? null}
          ghost={data?.naive ?? null}
          lineColor={current?.color ?? "var(--hard)"}
          onClick={onMapClick}
          onDragEnd={(role, p) => void setPin(role, p)}
        />
        {planning && (
          <div className="pointer-events-none absolute left-1/2 top-3 -translate-x-1/2 rounded-full bg-panel px-3 py-1 text-xs shadow">
            Planning…
          </div>
        )}
      </main>
    </div>
  );
}

function PlaceSearchDivider() {
  return <span className="ml-1 h-px flex-1 bg-line" aria-hidden />;
}
