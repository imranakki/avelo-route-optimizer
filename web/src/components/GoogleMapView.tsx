"use client";

import { useEffect, useRef, useState } from "react";
import type { Itinerary, Station } from "@/lib/api";
import { bootstrapGoogle, googleAuthFailed, importLibrary } from "@/lib/google";
import { resolveColor, STATION_COLORS, stationState, stationTip, type MapProps } from "./mapProps";

const QUEBEC = { lat: 46.813, lng: -71.225 };

// A quiet base map: greys and paper tones, no business POIs, so the routes and
// stations are the only colour on it.
const STYLE_LIGHT: google.maps.MapTypeStyle[] = [
  { elementType: "geometry", stylers: [{ color: "#efece5" }] },
  { elementType: "labels.text.fill", stylers: [{ color: "#6f6a60" }] },
  { elementType: "labels.text.stroke", stylers: [{ color: "#f4f1ea" }] },
  { featureType: "poi", stylers: [{ visibility: "off" }] },
  { featureType: "poi.park", elementType: "geometry", stylers: [{ color: "#e2e4d6" }, { visibility: "on" }] },
  { featureType: "road", elementType: "geometry", stylers: [{ color: "#ffffff" }] },
  { featureType: "road", elementType: "geometry.stroke", stylers: [{ color: "#dcd7cc" }] },
  { featureType: "road.highway", elementType: "geometry", stylers: [{ color: "#e9e4d8" }] },
  { featureType: "road", elementType: "labels.icon", stylers: [{ visibility: "off" }] },
  { featureType: "transit", stylers: [{ visibility: "off" }] },
  { featureType: "water", elementType: "geometry", stylers: [{ color: "#c9d3d6" }] },
  { featureType: "administrative", elementType: "geometry.stroke", stylers: [{ color: "#cfc9bb" }] },
];
const STYLE_DARK: google.maps.MapTypeStyle[] = [
  { elementType: "geometry", stylers: [{ color: "#1d1c19" }] },
  { elementType: "labels.text.fill", stylers: [{ color: "#8f8a7e" }] },
  { elementType: "labels.text.stroke", stylers: [{ color: "#151412" }] },
  { featureType: "poi", stylers: [{ visibility: "off" }] },
  { featureType: "poi.park", elementType: "geometry", stylers: [{ color: "#20261f" }, { visibility: "on" }] },
  { featureType: "road", elementType: "geometry", stylers: [{ color: "#2b2925" }] },
  { featureType: "road", elementType: "geometry.stroke", stylers: [{ color: "#1d1c19" }] },
  { featureType: "road.highway", elementType: "geometry", stylers: [{ color: "#35322c" }] },
  { featureType: "road", elementType: "labels.icon", stylers: [{ visibility: "off" }] },
  { featureType: "transit", stylers: [{ visibility: "off" }] },
  { featureType: "water", elementType: "geometry", stylers: [{ color: "#0f1416" }] },
];

const WALK_DASH: google.maps.IconSequence[] = [
  { icon: { path: "M 0,-1 0,1", strokeOpacity: 1, strokeWeight: 3, scale: 2.5 }, offset: "0", repeat: "11px" },
];

function pinSvg(color: string): string {
  return (
    "data:image/svg+xml;charset=UTF-8," +
    encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="36" viewBox="0 0 28 36"><path d="M14 35C14 35 3 21.5 3 13a11 11 0 0 1 22 0c0 8.5-11 22-11 22Z" fill="${color}" stroke="#f4f1ea" stroke-width="2"/><circle cx="14" cy="13" r="4" fill="#f4f1ea"/></svg>`,
    )
  );
}

type Props = MapProps & { onUnavailable: (reason: string) => void };

export default function GoogleMapView({
  stations,
  origin,
  destination,
  itinerary,
  ghost,
  lineColor,
  onClick,
  onStationClick,
  onDragEnd,
  onUnavailable,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<google.maps.Map | null>(null);
  const [ready, setReady] = useState(false);
  const handlers = useRef({ onClick, onStationClick, onDragEnd, onUnavailable });
  const markers = useRef<{ origin: google.maps.Marker | null; destination: google.maps.Marker | null }>({
    origin: null,
    destination: null,
  });
  const routeShapes = useRef<(google.maps.Polyline | google.maps.Marker)[]>([]);
  const ghostShapes = useRef<google.maps.Polyline[]>([]);
  const stationById = useRef(new Map<string, Station>());
  const tip = useRef<google.maps.InfoWindow | null>(null);

  useEffect(() => {
    handlers.current = { onClick, onStationClick, onDragEnd, onUnavailable };
  }, [onClick, onStationClick, onDragEnd, onUnavailable]);

  // Create the map once.
  useEffect(() => {
    if (!container.current || map.current) return;
    if (!bootstrapGoogle()) {
      queueMicrotask(() => handlers.current.onUnavailable("no key"));
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const { Map: GMap } = await importLibrary("maps");
        if (cancelled || !container.current) return;
        const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
        const m = new GMap(container.current, {
          center: QUEBEC,
          zoom: 13,
          styles: dark ? STYLE_DARK : STYLE_LIGHT,
          disableDefaultUI: true,
          zoomControl: true,
          zoomControlOptions: { position: google.maps.ControlPosition.RIGHT_TOP },
          gestureHandling: "greedy",
          clickableIcons: false,
          backgroundColor: dark ? "#151412" : "#efece5",
        });
        map.current = m;
        m.addListener("click", (e: google.maps.MapMouseEvent) => {
          if (e.latLng) handlers.current.onClick({ lat: e.latLng.lat(), lon: e.latLng.lng() });
        });
        tip.current = new google.maps.InfoWindow({ disableAutoPan: true, headerDisabled: true });
        m.data.setStyle((f) => {
          const state = f.getProperty("state") as keyof typeof STATION_COLORS;
          return {
            icon: {
              path: google.maps.SymbolPath.CIRCLE,
              scale: 4.5,
              fillColor: STATION_COLORS[state] ?? STATION_COLORS.ok,
              fillOpacity: 0.95,
              strokeColor: "#f4f1ea",
              strokeWeight: 1.2,
            },
            cursor: "pointer",
          };
        });
        m.data.addListener("mouseover", (e: google.maps.Data.MouseEvent) => {
          const s = stationById.current.get(String(e.feature.getProperty("id")));
          if (!s || !tip.current) return;
          tip.current.setContent(stationTip(s));
          tip.current.setPosition({ lat: s.lat, lng: s.lon });
          tip.current.open({ map: m });
        });
        m.data.addListener("mouseout", () => tip.current?.close());
        m.data.addListener("click", (e: google.maps.Data.MouseEvent) => {
          const s = stationById.current.get(String(e.feature.getProperty("id")));
          if (s) handlers.current.onStationClick(s);
          (e as unknown as { stop?: () => void }).stop?.();
        });
        setReady(true);
        if (await googleAuthFailed()) handlers.current.onUnavailable("auth");
      } catch (err) {
        handlers.current.onUnavailable(err instanceof Error ? err.message : "load");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Stations as a data layer.
  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    m.data.forEach((f) => m.data.remove(f));
    stationById.current = new Map(stations.map((s) => [s.id, s]));
    m.data.addGeoJson({
      type: "FeatureCollection",
      features: stations.map((s) => ({
        type: "Feature",
        properties: { id: s.id, state: stationState(s) },
        geometry: { type: "Point", coordinates: [s.lon, s.lat] },
      })),
    });
  }, [stations, ready]);

  // Route + ghost.
  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    routeShapes.current.forEach((s) => s.setMap(null));
    ghostShapes.current.forEach((s) => s.setMap(null));
    routeShapes.current = [];
    ghostShapes.current = [];
    const color = resolveColor(lineColor);
    const naiveColor = resolveColor("var(--naive)");
    const walkColor = resolveColor("var(--walk)");

    const path = (leg: Itinerary["legs"][number]) =>
      (leg.geometry && leg.geometry.length > 1 ? leg.geometry : [leg.from_coord, leg.to_coord]).map((c) => ({ lat: c.lat, lng: c.lon }));

    if (ghost && ghost !== itinerary) {
      for (const leg of ghost.legs) {
        if (leg.mode !== "RIDE") continue;
        ghostShapes.current.push(
          new google.maps.Polyline({ map: m, path: path(leg), strokeColor: naiveColor, strokeOpacity: 0.35, strokeWeight: 3, zIndex: 1 }),
        );
      }
    }
    if (itinerary) {
      const bounds = new google.maps.LatLngBounds();
      itinerary.legs.forEach((leg, i) => {
        const p = path(leg);
        p.forEach((pt) => bounds.extend(pt));
        if (leg.mode === "RIDE") {
          routeShapes.current.push(
            new google.maps.Polyline({ map: m, path: p, strokeColor: "#f4f1ea", strokeOpacity: 0.9, strokeWeight: 9, zIndex: 2 }),
            new google.maps.Polyline({ map: m, path: p, strokeColor: color, strokeOpacity: 1, strokeWeight: 5, zIndex: 3 }),
          );
          if (itinerary.legs[i + 1]?.mode === "RIDE") {
            routeShapes.current.push(
              new google.maps.Marker({
                map: m,
                position: { lat: leg.to_coord.lat, lng: leg.to_coord.lon },
                title: `Reset at ${leg.to_name}`,
                zIndex: 4,
                icon: { path: google.maps.SymbolPath.CIRCLE, scale: 7, fillColor: "#f4f1ea", fillOpacity: 1, strokeColor: color, strokeWeight: 3 },
              }),
            );
          }
        } else {
          routeShapes.current.push(
            new google.maps.Polyline({ map: m, path: p, strokeColor: walkColor, strokeOpacity: 0, icons: WALK_DASH, zIndex: 2 }),
          );
        }
      });
      m.fitBounds(bounds, { top: 60, bottom: 60, left: 60, right: 60 });
    }
  }, [itinerary, ghost, lineColor, ready]);

  // Origin / destination pins.
  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    for (const role of ["origin", "destination"] as const) {
      const point = role === "origin" ? origin : destination;
      let marker = markers.current[role];
      if (!point) {
        marker?.setMap(null);
        markers.current[role] = null;
        continue;
      }
      if (!marker) {
        marker = new google.maps.Marker({
          map: m,
          draggable: true,
          zIndex: 10,
          title: role === "origin" ? "Origin (drag to move)" : "Destination (drag to move)",
          icon: {
            url: pinSvg(resolveColor(role === "origin" ? "var(--origin)" : "var(--destination)")),
            scaledSize: new google.maps.Size(28, 36),
            anchor: new google.maps.Point(14, 35),
          },
        });
        marker.addListener("dragend", () => {
          const ll = marker!.getPosition();
          if (ll) handlers.current.onDragEnd(role, { lat: ll.lat(), lon: ll.lng() });
        });
        markers.current[role] = marker;
      }
      marker.setPosition({ lat: point.lat, lng: point.lon });
    }
    if (origin && destination && !itinerary) {
      const b = new google.maps.LatLngBounds({ lat: origin.lat, lng: origin.lon });
      b.extend({ lat: destination.lat, lng: destination.lon });
      m.fitBounds(b, 80);
    } else if ((origin && !destination) || (!origin && destination)) {
      const p = (origin ?? destination)!;
      m.panTo({ lat: p.lat, lng: p.lon });
      if ((m.getZoom() ?? 0) < 14) m.setZoom(14);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [origin, destination, ready]);

  return <div ref={container} className="h-full w-full" aria-label="Map of Québec City with àVélo stations" />;
}
