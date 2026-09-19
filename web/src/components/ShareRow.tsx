"use client";

import { useState } from "react";
import type { Itinerary } from "@/lib/api";

// Hand the plan to a navigation app. Google Maps takes the whole route as
// waypoints (every station and visit, in order, bicycling mode). Waze has no
// waypoints, so it navigates to the first station -- the next thing to reach.
function waypointsOf(it: Itinerary): { lat: number; lon: number }[] {
  const pts: { lat: number; lon: number }[] = [];
  for (const leg of it.legs) pts.push(leg.to_coord);
  pts.pop(); // the destination is passed separately
  return pts;
}

export function googleMapsUrl(it: Itinerary): string {
  const origin = it.legs[0].from_coord;
  const dest = it.legs[it.legs.length - 1].to_coord;
  const wp = waypointsOf(it)
    .slice(0, 9) // the URL scheme allows nine intermediate points
    .map((p) => `${p.lat.toFixed(6)},${p.lon.toFixed(6)}`)
    .join("|");
  const q = new URLSearchParams({
    api: "1",
    origin: `${origin.lat.toFixed(6)},${origin.lon.toFixed(6)}`,
    destination: `${dest.lat.toFixed(6)},${dest.lon.toFixed(6)}`,
    travelmode: "bicycling",
  });
  if (wp) q.set("waypoints", wp);
  return `https://www.google.com/maps/dir/?${q}`;
}

export function wazeUrl(it: Itinerary): { url: string; to: string } {
  const first = it.legs.find((l) => l.mode === "RIDE") ?? it.legs[it.legs.length - 1];
  const p = first.mode === "RIDE" ? first.from_coord : first.to_coord;
  const to = first.mode === "RIDE" ? first.from_name : first.to_name;
  return { url: `https://waze.com/ul?ll=${p.lat.toFixed(6)},${p.lon.toFixed(6)}&navigate=yes`, to };
}

export default function ShareRow({ it, title }: { it: Itinerary; title: string }) {
  const [copied, setCopied] = useState(false);
  const waze = wazeUrl(it);
  const share = async () => {
    const url = window.location.href;
    const text = `${title}: ${(it.total_seconds / 60).toFixed(0)} min, $${it.total_cost.toFixed(2)}`;
    try {
      if (navigator.share) {
        await navigator.share({ title: "àVélo trip", text, url });
        return;
      }
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // the user dismissed the share sheet, or clipboard is unavailable
    }
  };
  const cls = "inline-flex min-h-10 items-center gap-1.5 border border-rule-strong px-3 text-[12px] text-ink hover:bg-paper-2";
  return (
    <div className="mt-1 mb-4 flex flex-wrap gap-2 pl-[62px]">
      <a className={cls} href={googleMapsUrl(it)} target="_blank" rel="noopener noreferrer">
        <Pin /> Open in Google Maps
      </a>
      <a className={cls} href={waze.url} target="_blank" rel="noopener noreferrer" title={`Waze cannot take waypoints; this navigates to ${waze.to}`}>
        <Pin /> Waze to {waze.to}
      </a>
      <button type="button" className={cls} onClick={() => void share()}>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" />
          <path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4" />
        </svg>
        {copied ? "Link copied" : "Share"}
      </button>
    </div>
  );
}

function Pin() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M12 22s7-7.4 7-12a7 7 0 1 0-14 0c0 4.6 7 12 7 12Z" />
      <circle cx="12" cy="10" r="2.5" />
    </svg>
  );
}
