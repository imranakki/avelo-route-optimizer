# Web UI

Next.js front-end for the àVélo Route Optimizer API: search for places (or tap / drag
pins on the map), pick a bike type and plan, and every strategy is planned and drawn
on real streets — the constrained route, the direct ride, and the time/money frontier
in between, with reset stops marked.

```bash
npm install
npm run dev                                  # http://localhost:3000, proxies /api/* to API_URL
API_URL=http://127.0.0.1:8765 npm run dev    # API on another port
npm run build && npm start                   # production (standalone output)
```

Copy `.env.example` to `.env.local` to set the Google Maps browser key.

## Stack

Next.js (App Router, TypeScript), Tailwind, IBM Plex Sans/Mono + Instrument Serif.
Base map: Google Maps (JavaScript API, custom muted style) when a key is configured and
authorised, otherwise MapLibre GL with OpenFreeMap vector tiles — the switch is automatic
at runtime, so the app never shows a blank map. Both providers implement the same
`MapProps` contract (`src/components/mapProps.ts`).

Place search goes through the API (`/geocode`, `/geocode/place`): Google Places (New)
server-side when the API has a key, the OSM geocoder otherwise. The browser never talks
to Google for search, so a Maps authorisation failure cannot break it. A per-session id
groups keystrokes and the final selection the way Google bills autocomplete.

Trips can have several stops (add, reorder, remove) and return to the start; each leg of
a multi-stop trip has its own colour on the map and in the timetable, and each visit is
marked "bike docked". With a route highlighted, only the stations it uses stay on the map.
The whole trip (stops, round trip, bike, plan) lives in the URL hash, so a plan is
shareable; the selected itinerary can be handed to Google Maps (full route as waypoints,
bicycling) or Waze (to the first station — Waze takes no waypoints), or shared with the
system share sheet.

On phones the panel is a bottom sheet over a full-screen map, with a drag handle and
three snap heights; the map keeps the route above the sheet.

## Design

A route planner is a timetable, so the interface is set like one: the itinerary is a
vertical rail with monospaced clock times, the time/money trade-off is a small chart
you can click, and the result is a sentence in a serif ("Reset the clock once and save
$2.70 for 1.2 fewer minutes"). Paper and ink, one signal colour per strategy, no cards,
no gradients, no icons where a word will do.

## Notes

- MapLibre resolves its tile-parsing worker from `import.meta.url`, which is not an http
  URL under a bundler; the worker is served from `public/maplibre/` (committed, refreshed
  by `scripts/copy-maplibre-worker.mjs` on install) and set explicitly in `MapLibreView`.
- Colours are CSS custom properties; map providers resolve them to real colours before
  handing them to paint properties.
- After a Google Maps authorisation failure (`ApiNotActivatedMapError`, referrer not
  allowed), Google's script disables its own classes — which is why search is done
  server-side and the map falls back rather than retrying.
