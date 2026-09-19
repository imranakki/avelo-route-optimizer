"use client";

import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import type { Map as MLMap, MapLayerMouseEvent, MapMouseEvent, Marker } from "maplibre-gl";
import type { Coord, Itinerary, Station } from "@/lib/api";

export type MapPoint = { lat: number; lon: number };

type Props = {
  stations: Station[];
  origin: MapPoint | null;
  destination: MapPoint | null;
  itinerary: Itinerary | null; // the highlighted one
  ghost: Itinerary | null; // faint comparison (the naive route)
  lineColor: string;
  onClick: (p: MapPoint) => void;
  onDragEnd: (role: "origin" | "destination", p: MapPoint) => void;
};

// MapLibre derives its worker URL from import.meta.url, which is not an http(s) URL
// under a bundler; without this the worker is spawned from an empty URL and tiles
// never load. The file is copied to public/ by scripts/copy-maplibre-worker.mjs.
maplibregl.setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");

const STYLE_LIGHT = "https://tiles.openfreemap.org/styles/positron";
const STYLE_DARK = "https://tiles.openfreemap.org/styles/dark";
const QUEBEC: [number, number] = [-71.225, 46.813];

function legLine(leg: Itinerary["legs"][number]): GeoJSON.Feature<GeoJSON.LineString> {
  const pts: Coord[] = leg.geometry && leg.geometry.length > 1 ? leg.geometry : [leg.from_coord, leg.to_coord];
  return {
    type: "Feature",
    properties: { mode: leg.mode },
    geometry: { type: "LineString", coordinates: pts.map((c) => [c.lon, c.lat]) },
  };
}

function toCollection(it: Itinerary | null): GeoJSON.FeatureCollection {
  return { type: "FeatureCollection", features: it ? it.legs.map(legLine) : [] };
}

function resetPoints(it: Itinerary | null): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  if (it) {
    it.legs.forEach((leg, i) => {
      const next = it.legs[i + 1];
      if (leg.mode === "RIDE" && next?.mode === "RIDE") {
        features.push({
          type: "Feature",
          properties: { name: leg.to_name },
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
        name: s.name,
        bikes: s.bikes,
        docks: s.docks,
        elevation: s.elevation_m == null ? "?" : Math.round(s.elevation_m),
        state: !s.renting && !s.returning ? "down" : s.bikes === 0 ? "empty" : s.docks === 0 ? "full" : "ok",
      },
      geometry: { type: "Point", coordinates: [s.lon, s.lat] },
    })),
  };
}

// MapLibre paint values must be real colors; the UI passes CSS custom properties.
function resolveColor(value: string): string {
  const m = /^var\((--[\w-]+)\)$/.exec(value.trim());
  if (!m) return value;
  return getComputedStyle(document.documentElement).getPropertyValue(m[1]).trim() || "#0f6e56";
}

function makeMarker(role: "origin" | "destination"): Marker {
  const el = document.createElement("div");
  el.className = `marker marker-${role}`;
  el.title = role === "origin" ? "Origin (drag to move)" : "Destination (drag to move)";
  return new maplibregl.Marker({ element: el, draggable: true, anchor: "bottom" });
}

export default function MapView({ stations, origin, destination, itinerary, ghost, lineColor, onClick, onDragEnd }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MLMap | null>(null);
  const ready = useRef(false);
  const markers = useRef<{ origin: Marker | null; destination: Marker | null }>({ origin: null, destination: null });
  const handlers = useRef({ onClick, onDragEnd });
  const queue = useRef<((m: MLMap) => void)[]>([]);
  useEffect(() => {
    handlers.current = { onClick, onDragEnd };
  }, [onClick, onDragEnd]);

  // Create the map once.
  useEffect(() => {
    if (!container.current || map.current) return;
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const m = new maplibregl.Map({
      container: container.current,
      style: dark ? STYLE_DARK : STYLE_LIGHT,
      center: QUEBEC,
      zoom: 12.6,
      attributionControl: { compact: true },
    });
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
            "empty", "#b42318",
            "full", "#b54708",
            "down", "#8a8985",
            "#0f6e56",
          ],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1,
          "circle-opacity": 0.9,
        },
      });
      m.addLayer({
        id: "ghost-line",
        type: "line",
        source: "ghost",
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": "#c2410c", "line-width": 3, "line-opacity": 0.35, "line-dasharray": [1, 2] },
      });
      m.addLayer({
        id: "route-casing",
        type: "line",
        source: "route",
        filter: ["==", ["get", "mode"], "RIDE"],
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": "#ffffff", "line-width": 9, "line-opacity": 0.9 },
      });
      m.addLayer({
        id: "route-ride",
        type: "line",
        source: "route",
        filter: ["==", ["get", "mode"], "RIDE"],
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": resolveColor(lineColor), "line-width": 5 },
      });
      m.addLayer({
        id: "route-walk",
        type: "line",
        source: "route",
        filter: ["==", ["get", "mode"], "WALK"],
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": "#6d6c67", "line-width": 3, "line-dasharray": [0.5, 1.6] },
      });
      m.addLayer({
        id: "resets",
        type: "circle",
        source: "resets",
        paint: { "circle-radius": 7, "circle-color": "#ffffff", "circle-stroke-color": resolveColor(lineColor), "circle-stroke-width": 3 },
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
            `<div style="font:12px system-ui;color:#1a1a18"><b>${p.name}</b><br>${p.bikes} bikes · ${p.docks} docks · ${p.elevation} m</div>`,
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
          .setHTML(`<div style="font:12px system-ui;color:#1a1a18">Reset: dock &amp; re-unlock at<br><b>${(f.properties as { name: string }).name}</b></div>`)
          .addTo(m);
      });
      m.on("mouseleave", "resets", () => popup.remove());

      m.on("click", (e: MapMouseEvent) => handlers.current.onClick({ lat: e.lngLat.lat, lon: e.lngLat.lng }));
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Source/layer updates must wait for the style to load; queue them until then.
  const whenReady = (fn: (m: MLMap) => void) => {
    const m = map.current;
    if (!m) return;
    if (ready.current) fn(m);
    else queue.current.push(fn);
  };

  useEffect(() => {
    whenReady((m) => (m.getSource("stations") as maplibregl.GeoJSONSource).setData(stationCollection(stations)));
     
  }, [stations]);

  useEffect(() => {
    whenReady((m) => {
      (m.getSource("route") as maplibregl.GeoJSONSource).setData(toCollection(itinerary));
      (m.getSource("resets") as maplibregl.GeoJSONSource).setData(resetPoints(itinerary));
      (m.getSource("ghost") as maplibregl.GeoJSONSource).setData(toCollection(ghost && ghost !== itinerary ? ghost : null));
      const color = resolveColor(lineColor);
      m.setPaintProperty("route-ride", "line-color", color);
      m.setPaintProperty("resets", "circle-stroke-color", color);
      if (itinerary) {
        const b = new maplibregl.LngLatBounds();
        itinerary.legs.forEach((leg) => (leg.geometry ?? [leg.from_coord, leg.to_coord]).forEach((c) => b.extend([c.lon, c.lat])));
        m.fitBounds(b, { padding: { top: 60, bottom: 60, left: 60, right: 60 }, maxZoom: 15.5, duration: 600 });
      }
    });
     
  }, [itinerary, ghost, lineColor]);

  // Markers for origin / destination.
  useEffect(() => {
    whenReady((m) => {
      for (const role of ["origin", "destination"] as const) {
        const point = role === "origin" ? origin : destination;
        let marker = markers.current[role];
        if (!point) {
          marker?.remove();
          markers.current[role] = null;
          continue;
        }
        if (!marker) {
          marker = makeMarker(role);
          marker.on("dragend", () => {
            const ll = marker!.getLngLat();
            handlers.current.onDragEnd(role, { lat: ll.lat, lon: ll.lng });
          });
          markers.current[role] = marker;
          marker.setLngLat([point.lon, point.lat]).addTo(m);
        } else {
          marker.setLngLat([point.lon, point.lat]);
        }
      }
      if (origin && destination && !itinerary) {
        const b = new maplibregl.LngLatBounds([origin.lon, origin.lat], [origin.lon, origin.lat]);
        b.extend([destination.lon, destination.lat]);
        m.fitBounds(b, { padding: 80, maxZoom: 15, duration: 500 });
      } else if ((origin && !destination) || (!origin && destination)) {
        const p = (origin ?? destination)!;
        m.easeTo({ center: [p.lon, p.lat], zoom: Math.max(m.getZoom(), 13.5), duration: 400 });
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [origin, destination]);

  return <div ref={container} className="h-full w-full" aria-label="Map of Québec City with àVélo stations" />;
}
