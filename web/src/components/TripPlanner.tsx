"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, type CompareResponse, type Station, type Vehicle } from "@/lib/api";
import { GOOGLE_KEY } from "@/lib/google";
import type { MapPoint } from "./mapProps";
import PlaceSearch, { type Endpoint } from "./PlaceSearch";
import ResultsPanel, { choicesFrom } from "./ResultsPanel";
import Segmented from "./Segmented";

// Both maps touch `window` at import time, so they are client-only.
const GoogleMapView = dynamic(() => import("./GoogleMapView"), { ssr: false });
const MapLibreView = dynamic(() => import("./MapLibreView"), { ssr: false });

type Provider = "google" | "maplibre";
const LIMITS = [30, 45] as const;

const fmt = (p: MapPoint) => `${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}`;

// The trip lives in the URL hash so a plan can be shared or reloaded.
function readHash(): Partial<{ from: Endpoint; to: Endpoint; vehicle: Vehicle; limit: number }> {
  if (typeof window === "undefined") return {};
  try {
    const q = new URLSearchParams(window.location.hash.slice(1));
    const ep = (k: string): Endpoint | undefined => {
      const v = q.get(k);
      if (!v) return undefined;
      const [lat, lon, ...name] = v.split("|");
      const la = Number(lat);
      const lo = Number(lon);
      if (!Number.isFinite(la) || !Number.isFinite(lo)) return undefined;
      return { lat: la, lon: lo, name: name.join("|") || "Pinned location", label: fmt({ lat: la, lon: lo }) };
    };
    const limit = Number(q.get("limit"));
    return {
      from: ep("from"),
      to: ep("to"),
      vehicle: (q.get("bike") as Vehicle) || undefined,
      limit: LIMITS.includes(limit as 30 | 45) ? limit : undefined,
    };
  } catch {
    return {};
  }
}

export default function TripPlanner() {
  const initial = useMemo(() => readHash(), []);
  const [origin, setOrigin] = useState<Endpoint | null>(initial.from ?? null);
  const [destination, setDestination] = useState<Endpoint | null>(initial.to ?? null);
  const [vehicle, setVehicle] = useState<Vehicle>(initial.vehicle ?? "EFIT");
  const [limit, setLimit] = useState<number>(initial.limit ?? 30);
  const [stations, setStations] = useState<Station[]>([]);
  const [data, setData] = useState<CompareResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [planning, setPlanning] = useState(false);
  const [locating, setLocating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [apiDown, setApiDown] = useState(false);
  const [provider, setProvider] = useState<Provider>(GOOGLE_KEY ? "google" : "maplibre");
  const [mapNote, setMapNote] = useState<string | null>(null);
  const [sheet, setSheet] = useState(true);
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

  // Keep the URL in sync with the trip.
  useEffect(() => {
    const q = new URLSearchParams();
    if (origin) q.set("from", `${origin.lat.toFixed(5)}|${origin.lon.toFixed(5)}|${origin.name}`);
    if (destination) q.set("to", `${destination.lat.toFixed(5)}|${destination.lon.toFixed(5)}|${destination.name}`);
    if (vehicle !== "EFIT") q.set("bike", vehicle);
    if (limit !== 30) q.set("limit", String(limit));
    const hash = q.toString();
    window.history.replaceState(null, "", hash ? `#${hash}` : window.location.pathname);
  }, [origin, destination, vehicle, limit]);

  const fleet = useMemo(() => {
    const total: Record<string, number> = {};
    for (const s of stations) for (const [k, v] of Object.entries(s.bikes_by_type)) total[k] = (total[k] ?? 0) + v;
    return total;
  }, [stations]);

  // A pin dropped or dragged on the map: label it with the nearest named place.
  const setPin = useCallback(async (role: "origin" | "destination", p: MapPoint) => {
    const setter = role === "origin" ? setOrigin : setDestination;
    setter({ ...p, name: "Pinned location", label: fmt(p) });
    try {
      const place = await api.reverse(p);
      if (place) setter({ ...p, name: place.name, label: place.label ? `${place.label} · ${fmt(p)}` : fmt(p) });
    } catch {
      // keep the coordinate label
    }
  }, []);

  const nextRole = useCallback((): "origin" | "destination" => (!origin ? "origin" : "destination"), [origin]);

  const onMapClick = useCallback((p: MapPoint) => void setPin(nextRole(), p), [nextRole, setPin]);

  const onStationClick = useCallback(
    (s: Station) => {
      const setter = nextRole() === "origin" ? setOrigin : setDestination;
      setter({ lat: s.lat, lon: s.lon, name: s.name, label: `àVélo station · ${s.bikes} bikes, ${s.docks} docks` });
    },
    [nextRole],
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

  const plan = useCallback(async () => {
    if (!origin || !destination) return;
    planAbort.current?.abort();
    const ctrl = new AbortController();
    planAbort.current = ctrl;
    setPlanning(true);
    setError(null);
    try {
      const res = await api.compare(origin, destination, vehicle, limit, ctrl.signal);
      setData(res);
      const first = choicesFrom(res)[0];
      setSelected(first ? first.key : null);
      setSheet(true);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setData(null);
      setSelected(null);
      setError(err instanceof ApiError ? err.message : "The routing service is unreachable.");
    } finally {
      if (planAbort.current === ctrl) setPlanning(false);
    }
  }, [origin, destination, vehicle, limit]);

  // Plan automatically once both ends are set, and whenever an input changes.
  useEffect(() => {
    const t = setTimeout(() => {
      if (origin && destination) void plan();
      else setData(null);
    }, 0);
    return () => clearTimeout(t);
  }, [origin, destination, vehicle, limit, plan]);

  const onGoogleUnavailable = useCallback((reason: string) => {
    setProvider("maplibre");
    if (reason === "auth") {
      setMapNote("Google Maps rejected the key (enable the Maps JavaScript API for it). Showing OpenStreetMap instead.");
    }
  }, []);

  const choices = useMemo(() => (data ? choicesFrom(data) : []), [data]);
  const current = choices.find((c) => c.key === selected) ?? null;
  const mapProps = {
    stations,
    origin,
    destination,
    itinerary: current?.itinerary ?? null,
    ghost: data?.naive ?? null,
    lineColor: current?.color ?? "var(--limit)",
    onClick: onMapClick,
    onStationClick,
    onDragEnd: (role: "origin" | "destination", p: MapPoint) => void setPin(role, p),
  };

  return (
    <div className="grid h-full grid-rows-[minmax(0,1fr)_auto] md:grid-cols-[440px_minmax(0,1fr)] md:grid-rows-1">
      <aside
        className={`order-2 flex flex-col overflow-hidden border-t border-rule-strong bg-paper transition-[max-height] md:order-1 md:max-h-none md:border-r md:border-t-0 ${
          sheet ? "max-h-[62vh]" : "max-h-[52px]"
        }`}
      >
        <header className="flex items-baseline justify-between px-5 pt-4 pb-3">
          <h1 className="flex items-baseline gap-2">
            <span className="text-[15px] font-semibold tracking-tight text-ink">àVélo</span>
            <span className="serif text-[19px] italic text-ink-2">Route Optimizer</span>
          </h1>
          <span className="label hidden md:inline">Québec City</span>
          <button type="button" className="label md:hidden" onClick={() => setSheet((o) => !o)} aria-expanded={sheet}>
            {sheet ? "Hide" : "Show"}
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-5 pb-6">
          <div className="relative">
            <PlaceSearch role="origin" value={origin} onChange={setOrigin} onLocate={locate} locating={locating} autoFocus={!origin} />
            <PlaceSearch role="destination" value={destination} onChange={setDestination} />
            <button
              type="button"
              onClick={swap}
              disabled={!origin && !destination}
              title="Swap origin and destination"
              aria-label="Swap origin and destination"
              className="absolute right-0 top-[38px] grid h-8 w-8 place-items-center border border-rule-strong bg-paper text-muted hover:text-ink disabled:opacity-30"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
                <path d="M7 3v18M7 21l-4-4M7 21l4-4M17 21V3M17 3l4 4M17 3l-4 4" />
              </svg>
            </button>
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

          {!origin && !destination && !data && (
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
              <p className="mt-5 text-[12px] text-muted">Tap a station or anywhere on the map to set your start, then your destination.</p>
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

      <main className="relative order-1 min-h-[38vh] md:order-2 md:min-h-0">
        {provider === "google" ? <GoogleMapView {...mapProps} onUnavailable={onGoogleUnavailable} /> : <MapLibreView {...mapProps} />}
        {planning && (
          <div className="num pointer-events-none absolute left-1/2 top-3 -translate-x-1/2 border border-rule-strong bg-paper px-3 py-1 text-[11px] uppercase tracking-[0.1em] text-ink">
            Planning
          </div>
        )}
        {origin && !destination && (
          <div className="pointer-events-none absolute bottom-4 left-1/2 -translate-x-1/2 border border-rule-strong bg-paper px-3 py-1.5 text-[12px] text-ink shadow-[0_8px_24px_-12px_rgb(0_0_0/0.5)]">
            Now tap where you are going
          </div>
        )}
      </main>
    </div>
  );
}
