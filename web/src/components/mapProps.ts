import type { Itinerary, Station } from "@/lib/api";

export type MapPoint = { lat: number; lon: number };

/** The contract both map providers implement, so TripPlanner can swap them. */
export type MapProps = {
  stations: Station[];
  // The trip's stops in order (a round trip does not repeat the first one here).
  stops: (MapPoint | null)[];
  itinerary: Itinerary | null; // the highlighted one
  ghost: Itinerary | null; // faint comparison (the direct ride)
  lineColor: string; // a CSS custom property, e.g. "var(--limit)"
  onClick: (p: MapPoint) => void;
  onStationClick: (s: Station) => void;
  onDragEnd: (index: number, p: MapPoint) => void;
};

/** Pin colour by position: start, intermediate visits, end. */
export function stopColor(index: number, count: number): string {
  if (index === 0) return "var(--origin)";
  if (index === count - 1) return "var(--destination)";
  return "var(--ink)";
}

/** MapLibre and Google paint values must be real colours; the UI passes CSS custom properties. */
export function resolveColor(value: string): string {
  const m = /^var\((--[\w-]+)\)$/.exec(value.trim());
  if (!m) return value;
  return getComputedStyle(document.documentElement).getPropertyValue(m[1]).trim() || "#1d6b58";
}

export function stationState(s: Station): "ok" | "empty" | "full" | "down" {
  if (!s.renting && !s.returning) return "down";
  if (s.bikes === 0) return "empty";
  if (s.docks === 0) return "full";
  return "ok";
}

export const STATION_COLORS = { ok: "#1d6b58", empty: "#a8271b", full: "#9a6200", down: "#8a857a" } as const;

export function stationTip(s: Station): string {
  const elev = s.elevation_m == null ? "?" : Math.round(s.elevation_m);
  return `<div class="station-tip"><b>${escapeHtml(s.name)}</b><br><span class="num">${s.bikes}</span> bikes · <span class="num">${s.docks}</span> docks · <span class="num">${elev} m</span></div>`;
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}
