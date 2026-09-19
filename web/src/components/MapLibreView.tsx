"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { Map as MLMap, MapLayerMouseEvent, MapMouseEvent, Marker } from "maplibre-gl";
import type { Coord, Itinerary, Station } from "@/lib/api";
import { escapeHtml, resolveColor, STATION_COLORS, stationState, stopColor, type MapProps } from "./mapProps";

type Props = MapProps;

// MapLibre derives its worker URL from import.meta.url, which is not an http(s) URL
// under a bundler; without this the worker is spawned from an empty URL and tiles
// never load. The file is copied to public/ by scripts/copy-maplibre-worker.mjs.
maplibregl.setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");

const STYLE_LIGHT = "https://tiles.openfreemap.org/styles/positron";
const STYLE_DARK = "https://tiles.openfreemap.org/styles/dark";
const QUEBEC: [number, number] = [-71.225, 46.813];

function legLine(leg: Itinerary["legs"][number], color: string): GeoJSON.Feature<GeoJSON.LineString> {
  const pts: Coord[] = leg.geometry && leg.geometry.length > 1 ? leg.geometry : [leg.from_coord, leg.to_coord];
  return {
    type: "Feature",
    properties: { mode: leg.mode, color },
    geometry: { type: "LineString", coordinates: pts.map((c) => [c.lon, c.lat]) },
  };
}

function toCollection(it: Itinerary | null, colors: string[] = []): GeoJSON.FeatureCollection {
  return { type: "FeatureCollection", features: it ? it.legs.map((leg, i) => legLine(leg, colors[i] ?? "#1d6b58")) : [] };
}

function resetPoints(it: Itinerary | null, colors: string[] = []): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  if (it) {
    it.legs.forEach((leg, i) => {
      const next = it.legs[i + 1];
      if (leg.mode === "RIDE" && next?.mode === "RIDE") {
        features.push({
          type: "Feature",
          properties: { name: leg.to_name, color: colors[i] ?? "#1d6b58" },
          geometry: { type: "Point", coordinates: [leg.to_coord.lon, leg.to_coord.lat] },
        });
      }
    });
  }
  return { type: "FeatureCollection", features };
}

function stationCollection(stations: Station[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: stations.map((s) => ({
      type: "Feature",
      properties: {
        id: s.id,
        name: s.name,
        bikes: s.bikes,
        docks: s.docks,
        elevation: s.elevation_m == null ? "?" : Math.round(s.elevation_m),
        state: stationState(s),
      },
      geometry: { type: "Point", coordinates: [s.lon, s.lat] },
    })),
  };
}

function makeMarker(index: number, count: number): Marker {
  const el = document.createElement("div");
  el.className = "pin";
  el.style.background = resolveColor(stopColor(index, count));
  el.title = `Stop ${index + 1} (drag to move)`;
  if (count > 2) {
    const n = document.createElement("span");
    n.className = "pin-label";
    n.textContent = String(index + 1);
    el.append(n);
  }
  return new maplibregl.Marker({ element: el, draggable: true, anchor: "bottom" });
}

export default function MapLibreView({ stations, stops, itinerary, ghost, lineColor, legColors, bottomInset, onClick, onStationClick, onDragEnd }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MLMap | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const ready = useRef(false);
  const pins = useRef<Marker[]>([]);
  const handlers = useRef({ onClick, onStationClick, onDragEnd });
  const queue = useRef<((m: MLMap) => void)[]>([]);
  const stationById = useRef(new Map<string, Station>());
  useEffect(() => {
    handlers.current = { onClick, onStationClick, onDragEnd };
  }, [onClick, onStationClick, onDragEnd]);

  // Create the map once.
  useEffect(() => {
    if (!container.current || map.current) return;
    const probe = document.createElement("canvas");
    if (!probe.getContext("webgl2") && !probe.getContext("webgl")) {
      queueMicrotask(() => setProblem("This browser has no WebGL support, so the map cannot be drawn."));
      return;
    }
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const m = new maplibregl.Map({
      container: container.current,
      style: dark ? STYLE_DARK : STYLE_LIGHT,
      center: QUEBEC,
      zoom: 12.6,
      attributionControl: { compact: true },
    });
    m.on("error", (e) => {
      // Style or tile fetch failures: say so rather than leaving a blank canvas.
      const msg = e.error?.message ?? "";
      if (/style|Failed to fetch|NetworkError|sprite/i.test(msg)) {
        setProblem(`Map tiles could not be loaded (${msg.slice(0, 80)}). Check that tiles.openfreemap.org is reachable.`);
      }
    });
    m.on("load", () => setProblem(null));
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.addControl(new maplibregl.GeolocateControl({ positionOptions: { enableHighAccuracy: true } }), "top-right");

    m.on("load", () => {
      m.addSource("stations", { type: "geojson", data: stationCollection([]) });
      m.addSource("ghost", { type: "geojson", data: toCollection(null) });
      m.addSource("route", { type: "geojson", data: toCollection(null) });
      m.addSource("resets", { type: "geojson", data: resetPoints(null) });

      m.addLayer({
        id: "stations",
        type: "circle",
        source: "stations",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 2.5, 14, 5, 17, 8],
          "circle-color": [
            "match",
            ["get", "state"],
            "empty", STATION_COLORS.empty,
            "full", STATION_COLORS.full,
            "down", STATION_COLORS.down,
            STATION_COLORS.ok,
          ],
          "circle-stroke-color": "#f4f1ea",
          "circle-stroke-width": 1,
          "circle-opacity": 0.9,
        },
      });
      m.addLayer({
        id: "ghost-line",
        type: "line",
        source: "ghost",
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": resolveColor("var(--naive)"), "line-width": 3, "line-opacity": 0.35 },
      });
      m.addLayer({
        id: "route-casing",
        type: "line",
        source: "route",
        filter: ["==", ["get", "mode"], "RIDE"],
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": "#f4f1ea", "line-width": 9, "line-opacity": 0.9 },
      });
      m.addLayer({
        id: "route-ride",
        type: "line",
        source: "route",
        filter: ["==", ["get", "mode"], "RIDE"],
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": ["get", "color"], "line-width": 5 },
      });
      m.addLayer({
        id: "route-walk",
        type: "line",
        source: "route",
        filter: ["==", ["get", "mode"], "WALK"],
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": resolveColor("var(--walk)"), "line-width": 3, "line-dasharray": [0.5, 1.6] },
      });
      m.addLayer({
        id: "resets",
        type: "circle",
        source: "resets",
        paint: { "circle-radius": 7, "circle-color": "#f4f1ea", "circle-stroke-color": ["get", "color"], "circle-stroke-width": 3 },
      });

      const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 });
      m.on("mouseenter", "stations", (e: MapLayerMouseEvent) => {
        m.getCanvas().style.cursor = "pointer";
        const f = e.features?.[0];
        if (!f || f.geometry.type !== "Point") return;
        const p = f.properties as { name: string; bikes: number; docks: number; elevation: string };
        popup
          .setLngLat(f.geometry.coordinates as [number, number])
          .setHTML(
            `<div class="station-tip"><b>${escapeHtml(p.name)}</b><br><span class="num">${p.bikes}</span> bikes · <span class="num">${p.docks}</span> docks · <span class="num">${p.elevation} m</span></div>`,
          )
          .addTo(m);
      });
      m.on("mouseleave", "stations", () => {
        m.getCanvas().style.cursor = "";
        popup.remove();
      });
      m.on("mouseenter", "resets", (e: MapLayerMouseEvent) => {
        const f = e.features?.[0];
        if (!f || f.geometry.type !== "Point") return;
        popup
          .setLngLat(f.geometry.coordinates as [number, number])
          .setHTML(`<div class="station-tip">Reset: dock &amp; re-unlock at<br><b>${escapeHtml((f.properties as { name: string }).name)}</b></div>`)
          .addTo(m);
      });
      m.on("mouseleave", "resets", () => popup.remove());

      m.on("click", "stations", (e: MapLayerMouseEvent) => {
        const id = String(e.features?.[0]?.properties?.id ?? "");
        const s = stationById.current.get(id);
        if (s) {
          handlers.current.onStationClick(s);
          e.preventDefault();
        }
      });
      m.on("click", (e: MapMouseEvent) => {
        if (e.defaultPrevented) return;
        handlers.current.onClick({ lat: e.lngLat.lat, lon: e.lngLat.lng });
      });
      ready.current = true;
      queue.current.splice(0).forEach((fn) => fn(m));
    });

    map.current = m;
    if (process.env.NODE_ENV !== "production") (window as unknown as { __map?: MLMap }).__map = m; // for UI tests
    return () => {
      m.remove();
      map.current = null;
      ready.current = false;
    };
     
  }, []);

  // Source/layer updates must wait for the style to load; queue them until then.
  const whenReady = (fn: (m: MLMap) => void) => {
    const m = map.current;
    if (!m) return;
    if (ready.current) fn(m);
    else queue.current.push(fn);
  };

  useEffect(() => {
    stationById.current = new Map(stations.map((s) => [s.id, s]));
    whenReady((m) => (m.getSource("stations") as maplibregl.GeoJSONSource).setData(stationCollection(stations)));
     
  }, [stations]);

  useEffect(() => {
    whenReady((m) => {
      const base = resolveColor(lineColor);
      const colors = (itinerary?.legs ?? []).map((_, i) => (legColors[i] ? resolveColor(legColors[i]) : base));
      (m.getSource("route") as maplibregl.GeoJSONSource).setData(toCollection(itinerary, colors));
      (m.getSource("resets") as maplibregl.GeoJSONSource).setData(resetPoints(itinerary, colors));
      (m.getSource("ghost") as maplibregl.GeoJSONSource).setData(toCollection(ghost && ghost !== itinerary ? ghost : null));
      if (itinerary) {
        const b = new maplibregl.LngLatBounds();
        itinerary.legs.forEach((leg) => (leg.geometry ?? [leg.from_coord, leg.to_coord]).forEach((c) => b.extend([c.lon, c.lat])));
        m.fitBounds(b, { padding: { top: 60, bottom: 60 + bottomInset, left: 40, right: 40 }, maxZoom: 15.5, duration: 600 });
      }
    });
     
  }, [itinerary, ghost, lineColor, legColors, bottomInset]);

  // One draggable, numbered pin per stop.
  useEffect(() => {
    whenReady((m) => {
      pins.current.forEach((p) => p.remove());
      pins.current = [];
      const count = stops.length;
      const set = stops.map((p, i) => ({ p, i })).filter((x): x is { p: NonNullable<(typeof stops)[number]>; i: number } => !!x.p);
      for (const { p, i } of set) {
        const marker = makeMarker(i, count);
        marker.on("dragend", () => {
          const ll = marker.getLngLat();
          handlers.current.onDragEnd(i, { lat: ll.lat, lon: ll.lng });
        });
        marker.setLngLat([p.lon, p.lat]).addTo(m);
        pins.current.push(marker);
      }
      if (set.length >= 2 && !itinerary) {
        const b = new maplibregl.LngLatBounds();
        set.forEach(({ p }) => b.extend([p.lon, p.lat]));
        m.fitBounds(b, { padding: { top: 80, bottom: 80 + bottomInset, left: 60, right: 60 }, maxZoom: 15, duration: 500 });
      } else if (set.length === 1) {
        m.easeTo({ center: [set[0].p.lon, set[0].p.lat], zoom: Math.max(m.getZoom(), 13.5), duration: 400 });
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stops, bottomInset]);

  return (
    <div className="relative h-full w-full">
      <div ref={container} className="h-full w-full" aria-label="Map of Québec City with àVélo stations" />
      {problem && (
        <div role="alert" className="absolute inset-x-4 top-4 rounded-lg border border-warn/40 bg-panel px-3 py-2 text-[13px] text-warn shadow">
          {problem}
        </div>
      )}
    </div>
  );
}
