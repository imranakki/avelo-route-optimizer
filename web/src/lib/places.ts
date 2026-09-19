// Place search, through the API (Google Places when the server has a key, OSM
// otherwise). A session id groups the keystrokes and the final selection, which is
// how Google bills autocomplete; a new session starts after each selection.

import { api, type Coord } from "./api";

export type Suggestion = {
  id: string;
  name: string;
  label: string;
  coord?: Coord; // present immediately for OSM results
  resolve?: () => Promise<Coord | null>; // fetches the location for Google results
};

let session = newSession();

function newSession(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : Math.random().toString(36).slice(2);
}

export async function suggest(query: string, signal?: AbortSignal): Promise<Suggestion[]> {
  const places = await api.geocode(query, session, signal);
  return places.map((p) => {
    if (p.lat != null && p.lon != null) {
      return { id: `${p.lat},${p.lon},${p.name}`, name: p.name, label: p.label, coord: { lat: p.lat, lon: p.lon } };
    }
    const id = p.place_id ?? p.name;
    const used = session;
    return {
      id,
      name: p.name,
      label: p.label,
      resolve: async () => {
        try {
          const full = await api.place(id, used);
          session = newSession();
          return full.lat != null && full.lon != null ? { lat: full.lat, lon: full.lon } : null;
        } catch {
          return null;
        }
      },
    };
  });
}
