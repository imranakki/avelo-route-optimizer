# Web UI

Next.js front-end for the àVélo Route Optimizer API: search for places (or click /
drag pins on the map), pick a bike type, and the trip is planned and drawn on real
streets — every strategy side by side, with resets marked.

```bash
npm install          # also copies the MapLibre worker into public/ (postinstall)
npm run dev          # http://localhost:3000, proxies /api/* to API_URL (default :8000)
API_URL=http://127.0.0.1:8765 npm run dev   # API elsewhere
npm run build && npm start                  # production
```

Stack: Next.js (App Router, TypeScript), Tailwind, MapLibre GL with OpenFreeMap vector
tiles. Place search and reverse geocoding go through the API's `/geocode` endpoints
(Photon/OSM behind a small cache), so the browser only ever talks to one origin.

Notes:
- MapLibre resolves its tile-parsing worker from `import.meta.url`, which is not an
  http URL under a bundler; `scripts/copy-maplibre-worker.mjs` serves the worker from
  `public/maplibre/` and `MapView.tsx` sets it explicitly. Without this the map
  silently never loads tiles.
- Colours are CSS custom properties; `MapView` resolves them to real colours before
  handing them to MapLibre paint properties.
