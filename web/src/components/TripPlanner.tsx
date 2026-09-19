"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, api2, ApiError, type Station, type TripResponse, type Vehicle } from "@/lib/api";
import { GOOGLE_KEY } from "@/lib/google";
import { legColorsFor, type MapPoint } from "./mapProps";
import PlaceSearch, { type Endpoint } from "./PlaceSearch";
import ResultsPanel, { choicesFrom } from "./ResultsPanel";
import Segmented from "./Segmented";

// Both maps touch `window` at import time, so they are client-only.
const GoogleMapView = dynamic(() => import("./GoogleMapView"), { ssr: false });
const MapLibreView = dynamic(() => import("./MapLibreView"), { ssr: false });

type Provider = "google" | "maplibre";
const LIMITS = [30, 45] as const;

const fmt = (p: MapPoint) => `${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}`;

// The trip lives in the URL hash so a plan can be shared or reloaded:
//   #stops=lat|lon|name;lat|lon|name;…&round=1&bike=ICONIC&limit=45
function readHash(): Partial<{ stops: (Endpoint | null)[]; round: boolean; vehicle: Vehicle; limit: number }> {
  if (typeof window === "undefined") return {};
  try {
    const q = new URLSearchParams(window.location.hash.slice(1));
    const stops = (q.get("stops") ?? "")
      .split(";")
      .filter(Boolean)
      .map((v): Endpoint | null => {
        const [lat, lon, ...name] = v.split("|");
        const la = Number(lat);
        const lo = Number(lon);
        if (!Number.isFinite(la) || !Number.isFinite(lo)) return null;
        return { lat: la, lon: lo, name: name.join("|") || "Pinned location", label: fmt({ lat: la, lon: lo }) };
      });
    const limit = Number(q.get("limit"));
    return {
      stops: stops.length >= 2 ? stops : undefined,
      round: q.get("round") === "1",
      vehicle: (q.get("bike") as Vehicle) || undefined,
      limit: LIMITS.includes(limit as 30 | 45) ? limit : undefined,
    };
  } catch {
    return {};
  }
}

const MAX_STOPS = 6;

export default function TripPlanner() {
  const initial = useMemo(() => readHash(), []);
  // stops[0] is the start, stops[last] the end; entries in between are visits.
  const [stops, setStops] = useState<(Endpoint | null)[]>(initial.stops ?? [null, null]);
  const [roundTrip, setRoundTrip] = useState<boolean>(initial.round ?? false);
  const [vehicle, setVehicle] = useState<Vehicle>(initial.vehicle ?? "EFIT");
  const [limit, setLimit] = useState<number>(initial.limit ?? 30);
  const [stations, setStations] = useState<Station[]>([]);
  const [data, setData] = useState<TripResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [planning, setPlanning] = useState(false);
  const [locating, setLocating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [apiDown, setApiDown] = useState(false);
  const [provider, setProvider] = useState<Provider>(GOOGLE_KEY ? "google" : "maplibre");
  const [mapNote, setMapNote] = useState<string | null>(null);
  // Mobile bottom sheet: three snap heights, dragged by the handle.
  type Snap = "min" | "peek" | "full";
  const SNAP: Record<Snap, number> = { min: 96, peek: 0.5, full: 0.92 };
  const [snap, setSnap] = useState<Snap>("peek");
  const [isMobile, setIsMobile] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 767px)");
    const update = () => setIsMobile(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  const [drag, setDrag] = useState<{ startY: number; startH: number; h: number } | null>(null);
  const sheetPx = (k: Snap) => (typeof window === "undefined" ? 400 : SNAP[k] < 1 ? window.innerHeight * SNAP[k] : SNAP[k]);
  const onHandleDown = (e: React.PointerEvent) => {
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    setDrag({ startY: e.clientY, startH: sheetPx(snap), h: sheetPx(snap) });
  };
  const onHandleMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const h = Math.max(SNAP.min, Math.min(window.innerHeight * SNAP.full, drag.startH + (drag.startY - e.clientY)));
    setDrag({ ...drag, h });
  };
  const onHandleUp = () => {
    if (!drag) return;
    const h = drag.h;
    const nearest = (["min", "peek", "full"] as Snap[]).reduce((a, b) => (Math.abs(sheetPx(a) - h) <= Math.abs(sheetPx(b) - h) ? a : b));
    setSnap(nearest);
    setDrag(null);
  };
  const planAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    api
      .stations()
      .then((s) => {
        setStations(s);
        setApiDown(false);
      })
      .catch(() => {
        setStations([]);
        setApiDown(true);
      });
  }, []);

  const complete = stops.every((s): s is Endpoint => s !== null) && stops.length >= 2;
  const filled = stops.filter((s): s is Endpoint => s !== null);

  // Keep the URL in sync with the trip.
  useEffect(() => {
    const q = new URLSearchParams();
    if (filled.length > 0) {
      q.set("stops", stops.map((s) => (s ? `${s.lat.toFixed(5)}|${s.lon.toFixed(5)}|${s.name.replace(/[;|]/g, " ")}` : "")).join(";"));
    }
    if (roundTrip) q.set("round", "1");
    if (vehicle !== "EFIT") q.set("bike", vehicle);
    if (limit !== 30) q.set("limit", String(limit));
    const hash = q.toString();
    window.history.replaceState(null, "", hash ? `#${hash}` : window.location.pathname);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stops, roundTrip, vehicle, limit]);

  const fleet = useMemo(() => {
    const total: Record<string, number> = {};
    for (const s of stations) for (const [k, v] of Object.entries(s.bikes_by_type)) total[k] = (total[k] ?? 0) + v;
    return total;
  }, [stations]);

  const setStop = useCallback((index: number, value: Endpoint | null) => {
    setStops((prev) => prev.map((s, i) => (i === index ? value : s)));
  }, []);

  // A pin dropped or dragged on the map: label it with the nearest named place.
  const setPin = useCallback(
    async (index: number, p: MapPoint) => {
      setStop(index, { ...p, name: "Pinned location", label: fmt(p) });
      try {
        const place = await api.reverse(p);
        if (place) setStop(index, { ...p, name: place.name, label: place.label ? `${place.label} · ${fmt(p)}` : fmt(p) });
      } catch {
        // keep the coordinate label
      }
    },
    [setStop],
  );

  // The first empty slot, else the last stop (so a tap always does something).
  const nextIndex = useCallback((): number => {
    const empty = stops.findIndex((s) => s === null);
    return empty >= 0 ? empty : stops.length - 1;
  }, [stops]);

  const onMapClick = useCallback((p: MapPoint) => void setPin(nextIndex(), p), [nextIndex, setPin]);

  const onStationClick = useCallback(
    (s: Station) => setStop(nextIndex(), { lat: s.lat, lon: s.lon, name: s.name, label: `àVélo station · ${s.bikes} bikes, ${s.docks} docks` }),
    [nextIndex, setStop],
  );

  const addStop = () => setStops((prev) => (prev.length >= MAX_STOPS ? prev : [...prev.slice(0, -1), null, prev[prev.length - 1]]));
  const removeStop = (index: number) => setStops((prev) => (prev.length <= 2 ? prev : prev.filter((_, i) => i !== index)));
  const moveStop = (index: number, dir: -1 | 1) =>
    setStops((prev) => {
      const j = index + dir;
      if (j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[index], next[j]] = [next[j], next[index]];
      return next;
    });

  const locate = () => {
    if (!navigator.geolocation) return setError("Geolocation is not available in this browser.");
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocating(false);
        void setPin(0, { lat: pos.coords.latitude, lon: pos.coords.longitude });
      },
      () => {
        setLocating(false);
        setError("Could not get your location.");
      },
      { enableHighAccuracy: true, timeout: 8000 },
    );
  };

  const reverse = () => setStops((prev) => [...prev].reverse());

  const plan = useCallback(async () => {
    if (!complete) return;
    planAbort.current?.abort();
    const ctrl = new AbortController();
    planAbort.current = ctrl;
    setPlanning(true);
    setError(null);
    try {
      const res = await api2.trip(filled, roundTrip, vehicle, limit, ctrl.signal);
      setData(res);
      const first = choicesFrom(res)[0];
      setSelected(first ? first.key : null);
      setSnap((k) => (k === "min" ? "peek" : k));
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setData(null);
      setSelected(null);
      setError(err instanceof ApiError ? err.message : "The routing service is unreachable.");
    } finally {
      if (planAbort.current === ctrl) setPlanning(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stops, roundTrip, vehicle, limit, complete]);

  // Plan automatically once every stop is set, and whenever an input changes.
  useEffect(() => {
    const t = setTimeout(() => {
      if (complete) void plan();
      else setData(null);
    }, 0);
    return () => clearTimeout(t);
  }, [complete, plan]);

  const onGoogleUnavailable = useCallback((reason: string) => {
    setProvider("maplibre");
    if (reason === "auth") {
      setMapNote("Google Maps rejected the key (enable the Maps JavaScript API for it). Showing OpenStreetMap instead.");
    }
  }, []);

  const choices = useMemo(() => (data ? choicesFrom(data) : []), [data]);
  const current = choices.find((c) => c.key === selected) ?? null;

  // With a route highlighted, only the stations it uses stay on the map: the
  // other 200 dots are noise once the decision is "where do I dock".
  const visibleStations = useMemo(() => {
    if (!current) return stations;
    const used = new Set<string>();
    for (const leg of current.itinerary.legs) {
      if (leg.mode !== "RIDE") continue;
      for (const c of [leg.from_coord, leg.to_coord]) {
        const hit = stations.find((s) => Math.abs(s.lat - c.lat) < 1e-4 && Math.abs(s.lon - c.lon) < 1e-4);
        if (hit) used.add(hit.id);
      }
    }
    return stations.filter((s) => used.has(s.id));
  }, [current, stations]);

  const mapProps = {
    stations: visibleStations,
    stops,
    itinerary: current?.itinerary ?? null,
    ghost: data?.naive ?? null,
    lineColor: current?.color ?? "var(--limit)",
    legColors: legColorsFor(current?.itinerary ?? null, current?.color ?? "var(--limit)"),
    bottomInset: isMobile ? sheetPx(snap) : 0,
    onClick: onMapClick,
    onStationClick,
    onDragEnd: (index: number, p: MapPoint) => void setPin(index, p),
  };
  const anyStop = filled.length > 0;

  const sheetStyle = { ["--sheet" as string]: `${drag ? drag.h : sheetPx(snap)}px` };

  return (
    <div className="relative h-full md:grid md:grid-cols-[440px_minmax(0,1fr)]">
      <aside
        style={sheetStyle}
        className={`absolute inset-x-0 bottom-0 z-20 flex h-[var(--sheet)] flex-col overflow-hidden border-t border-rule-strong bg-paper shadow-[0_-12px_32px_-16px_rgb(0_0_0/0.45)] md:static md:z-auto md:h-auto md:border-r md:border-t-0 md:shadow-none ${
          drag ? "" : "transition-[height] duration-300 ease-out"
        }`}
      >
        <div
          role="separator"
          aria-label="Drag to resize"
          onPointerDown={onHandleDown}
          onPointerMove={onHandleMove}
          onPointerUp={onHandleUp}
          onPointerCancel={onHandleUp}
          onClick={() => setSnap((k) => (k === "full" ? "peek" : "full"))}
          className="flex h-6 shrink-0 touch-none cursor-grab items-center justify-center md:hidden"
        >
          <span className="h-1 w-10 rounded-full bg-rule-strong" />
        </div>
        <header className="flex items-baseline justify-between px-5 pb-3 md:pt-4">
          <h1 className="flex items-baseline gap-2">
            <span className="text-[15px] font-semibold tracking-tight text-ink">àVélo</span>
            <span className="serif text-[19px] italic text-ink-2">Route Optimizer</span>
          </h1>
          <span className="label hidden md:inline">Québec City</span>
          {data && (
            <span className="num text-[12px] text-ink md:hidden">
              {current ? `${(current.itinerary.total_seconds / 60).toFixed(0)} min · $${current.itinerary.total_cost.toFixed(2)}` : ""}
            </span>
          )}
        </header>

        <div className="flex-1 overflow-y-auto px-5 pb-6">
          <div className="relative">
            {stops.map((stop, i) => {
              const role = i === 0 ? "origin" : i === stops.length - 1 ? "destination" : "via";
              return (
                <div key={i} className="group relative">
                  <PlaceSearch
                    role={role}
                    index={i}
                    value={stop}
                    onChange={(v) => setStop(i, v)}
                    onLocate={i === 0 ? locate : undefined}
                    locating={locating}
                    autoFocus={i === 0 && !stop}
                  />
                  {role === "via" && (
                    <div className="absolute right-0 top-2 flex gap-0.5">
                      <IconButton label="Move up" onClick={() => moveStop(i, -1)} disabled={i <= 1}>
                        <path d="M7 11l5-5 5 5" />
                      </IconButton>
                      <IconButton label="Move down" onClick={() => moveStop(i, 1)} disabled={i >= stops.length - 2}>
                        <path d="M7 13l5 5 5-5" />
                      </IconButton>
                      <IconButton label="Remove stop" onClick={() => removeStop(i)}>
                        <path d="M6 6l12 12M18 6L6 18" />
                      </IconButton>
                    </div>
                  )}
                </div>
              );
            })}
            <div className="mt-2 flex items-center justify-between gap-2">
              <button
                type="button"
                onClick={addStop}
                disabled={stops.length >= MAX_STOPS}
                className="label inline-flex items-center gap-1.5 py-1 text-ink hover:text-limit disabled:opacity-40"
              >
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden>
                  <path d="M12 5v14M5 12h14" />
                </svg>
                Add a stop
              </button>
              <label className="label inline-flex cursor-pointer items-center gap-2 py-1 text-ink">
                <input
                  type="checkbox"
                  checked={roundTrip}
                  onChange={(e) => setRoundTrip(e.target.checked)}
                  className="h-3.5 w-3.5 appearance-none border border-rule-strong bg-paper checked:bg-ink"
                />
                Return to start
              </label>
              <button
                type="button"
                onClick={reverse}
                disabled={filled.length < 2}
                title="Reverse the order of the stops"
                className="label inline-flex items-center gap-1.5 py-1 text-ink hover:text-limit disabled:opacity-40"
              >
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden>
                  <path d="M7 3v18M7 21l-4-4M7 21l4-4M17 21V3M17 3l4 4M17 3l-4 4" />
                </svg>
                Reverse
              </button>
            </div>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-3">
            <Segmented<Vehicle>
              label="Bike"
              value={vehicle}
              onChange={setVehicle}
              options={[
                { value: "EFIT", label: "Electric", hint: fleet.EFIT !== undefined ? `${fleet.EFIT} available` : "EFIT" },
                { value: "ICONIC", label: "Mechanical", hint: fleet.ICONIC !== undefined ? `${fleet.ICONIC} available` : "ICONIC" },
              ]}
            />
            <Segmented<number>
              label="Plan"
              value={limit}
              onChange={setLimit}
              options={LIMITS.map((l) => ({ value: l, label: `${l} min`, hint: "per ride" }))}
            />
          </div>

          {apiDown && (
            <p role="alert" className="mt-4 border-l-2 border-warn pl-3 text-[13px] text-ink-2">
              The routing API is not reachable. Start it with <code className="num">make run</code>, or point the UI at it with{" "}
              <code className="num">API_URL=http://127.0.0.1:PORT npm run dev</code>.
            </p>
          )}
          {mapNote && <p className="mt-4 border-l-2 border-rule-strong pl-3 text-[12px] text-muted">{mapNote}</p>}

          {!anyStop && !data && (
            <div className="mt-8">
              <p className="serif text-[26px] leading-[1.15] text-ink">
                One bike, one trip, <span className="italic">no overage</span>.
              </p>
              <p className="mt-3 text-[14px] leading-relaxed text-ink-2">
                àVélo bills {limit} minutes per ride and $0.30 for every minute past it. Docking at a station and unlocking the same bike
                again restarts the clock. This planner finds where to do that — or shows you exactly what riding straight through costs.
              </p>
              <dl className="mt-5 grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-1.5 text-[12px] text-muted">
                <dt>
                  <i className="block h-2 w-2 rounded-full bg-limit" />
                </dt>
                <dd>bikes and docks available</dd>
                <dt>
                  <i className="block h-2 w-2 rounded-full bg-danger" />
                </dt>
                <dd>no bikes to take</dd>
                <dt>
                  <i className="block h-2 w-2 rounded-full bg-warn" />
                </dt>
                <dd>no docks to reset in</dd>
              </dl>
              <p className="mt-5 text-[12px] text-muted">
                Tap a station or anywhere on the map to set your start, then your destination. Add stops to visit several places, and
                tick “Return to start” for a round trip.
              </p>
            </div>
          )}

          {error && (
            <p role="alert" className="mt-4 border-l-2 border-danger pl-3 text-[13px] text-ink-2">
              {error}
            </p>
          )}

          {planning && !data && (
            <p className="num mt-6 text-[12px] text-muted" aria-live="polite">
              Planning…
            </p>
          )}

          {data && (
            <div className="mt-6">
              <ResultsPanel data={data} choices={choices} selectedKey={selected} onSelect={setSelected} limitMinutes={limit} />
            </div>
          )}
        </div>
      </aside>

      <main className="absolute inset-0 md:static">
        {provider === "google" ? <GoogleMapView {...mapProps} onUnavailable={onGoogleUnavailable} /> : <MapLibreView {...mapProps} />}
        {planning && (
          <div className="num pointer-events-none absolute left-1/2 top-3 -translate-x-1/2 border border-rule-strong bg-paper px-3 py-1 text-[11px] uppercase tracking-[0.1em] text-ink">
            Planning
          </div>
        )}
        {anyStop && !complete && (
          <div className="pointer-events-none absolute bottom-4 left-1/2 -translate-x-1/2 border border-rule-strong bg-paper px-3 py-1.5 text-[12px] text-ink shadow-[0_8px_24px_-12px_rgb(0_0_0/0.5)]">
            Now tap stop {nextIndex() + 1}
            {nextIndex() === stops.length - 1 ? " — where you are going" : ""}
          </div>
        )}
      </main>
    </div>
  );
}

function IconButton({ label, onClick, disabled, children }: { label: string; onClick: () => void; disabled?: boolean; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="grid h-8 w-8 place-items-center text-muted hover:text-ink disabled:opacity-25"
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
        {children}
      </svg>
    </button>
  );
}
