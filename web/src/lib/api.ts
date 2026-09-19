// Typed client for the àVélo Route Optimizer API.
//
// In the browser every call goes to `/api/...`, which next.config.ts rewrites to the
// FastAPI service. That keeps the browser on one origin (no CORS) and lets the API
// URL be a deployment setting rather than something baked into the bundle.

export type Coord = { lat: number; lon: number };
export type LegMode = "WALK" | "RIDE";
export type Risk = "LOW" | "MEDIUM" | "HIGH" | "INFEASIBLE";
export type Vehicle = "EFIT" | "BOOST" | "ICONIC" | "FIT";

export type Leg = {
  mode: LegMode;
  from_name: string;
  to_name: string;
  from_coord: Coord;
  to_coord: Coord;
  distance_m: number;
  duration_seconds: number;
  cycling_seconds: number;
  traffic_light_seconds: number;
  docking_seconds: number;
  elevation_gain_m: number;
  risk: Risk;
  overage_cost: number;
  geometry: Coord[] | null;
};

export type Itinerary = {
  legs: Leg[];
  total_seconds: number;
  total_distance_m: number;
  total_cost: number;
  overall_risk: Risk;
  num_transfers: number;
};

export type SearchStats = {
  labels_generated: number;
  labels_pruned: number;
  labels_settled: number;
  frontier_size: number;
  pruned_fraction: number;
  planning_ms: number;
  walk_legs_street_routed: boolean;
};

export type CompareResponse = {
  naive: Itinerary | null;
  hard_constraint: Itinerary | null;
  options: Itinerary[];
  stats: SearchStats;
};

export type Station = {
  id: string;
  name: string;
  lat: number;
  lon: number;
  elevation_m: number | null;
  bikes: number;
  docks: number;
  bikes_by_type: Record<string, number>;
  renting: boolean;
  returning: boolean;
};

export type Place = {
  name: string;
  label: string;
  lat: number;
  lon: number;
  kind: string;
};

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

async function get<T>(path: string, params: Record<string, string | number | boolean>, signal?: AbortSignal): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) qs.set(k, String(v));
  const res = await fetch(`/api${path}?${qs}`, { signal });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // non-JSON error body: keep the status text
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

export const api = {
  geocode: (q: string, signal?: AbortSignal) =>
    get<{ query: string; results: Place[] }>("/geocode", { q, limit: 6 }, signal).then((r) => r.results),

  reverse: (c: Coord, signal?: AbortSignal) =>
    get<Place | null>("/geocode/reverse", { lat: c.lat, lon: c.lon }, signal),

  stations: () => get<{ count: number; stations: Station[] }>("/stations", {}).then((r) => r.stations),

  compare: (from: Coord, to: Coord, vehicle: Vehicle, signal?: AbortSignal) =>
    get<CompareResponse>(
      "/route/compare",
      {
        from_lat: from.lat,
        from_lon: from.lon,
        to_lat: to.lat,
        to_lon: to.lon,
        vehicle,
        max_solutions: 8,
        geometry: true,
      },
      signal,
    ),
};

export const minutes = (s: number) => `${(s / 60).toFixed(1)} min`;
export const money = (d: number) => `$${d.toFixed(2)}`;
export const km = (m: number) => (m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`);
