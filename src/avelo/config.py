"""Central configuration.

Every magic number in this project lives here, not scattered through the code.

Why this matters: your evaluation chapter will compare model variants
(e.g. "does traffic-light modelling actually help?"). That comparison is only
credible if a variant is a *config change*, not a code edit. Hard-coded constants
make experiments impossible to reproduce.

Values can be overridden by environment variables or a local `.env` file, e.g.::

    AVELO_RIDE_LIMIT_MINUTES=45 uvicorn avelo.api.app:app
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AVELO_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Upstream data sources -------------------------------------------------
    gbfs_root: str = "https://quebec.publicbikesystem.net/customer/gbfs/v2/gbfs.json"
    gbfs_language: str = "fr"
    elevation_api: str = "https://api.open-meteo.com/v1/elevation"
    http_timeout_seconds: float = 20.0

    # --- Real street distances (OSRM) --------------------------------------------
    # Station-to-station distances come from a real road network instead of
    # haversine * detour_factor. The full matrix is fetched once and cached on disk
    # forever (streets do not move), so after the first run this costs nothing.
    #
    # Verified 2026-09-19: FOSSGIS runs public OSRM instances with genuine BICYCLE
    # and FOOT profiles (router.project-osrm.org, by contrast, only serves the car
    # network whatever profile you name). Both expose the standard OSRM API, so
    # self-hosting later is a URL change.
    osrm_root: str = "https://routing.openstreetmap.de/routed-bike"
    osrm_walk_root: str = "https://routing.openstreetmap.de/routed-foot"
    use_osrm: bool = True
    osrm_walk_legs: bool = True  # route walk legs live (2 small calls/request)
    osrm_table_max_coords: int = 100  # server limit per /table request
    osrm_request_delay_seconds: float = 0.25  # be polite to a shared public server

    # How long a fetched GBFS payload stays fresh. The feed advertises its own TTL
    # (~30s) but we do not want to hammer a public endpoint during development.
    station_info_ttl_seconds: float = 3600.0  # static data; changes rarely
    station_status_ttl_seconds: float = 30.0  # live data; matches feed TTL

    cache_dir: str = "data/cache"

    # --- The constraint we are routing against ---------------------------------
    # VERIFIED 2026-09-07 from the live system_pricing_plans feed: àVélo sells
    # 30-minute and 45-minute plans, with $0.30/min charged beyond the limit.
    # (The original project spec assumed 15 minutes; that was wrong.)
    ride_limit_minutes: float = 30.0
    overage_cost_per_minute: float = 0.30

    # --- Cost-model parameters -------------------------------------------------
    # The model's knobs. Defaults are documented in docs/cost-model.md; the
    # evaluation's ablation (docs/evaluation.md) shows what the grade model buys.
    speed_electric_kmh: float = 17.0
    speed_mechanical_kmh: float = 13.0

    # Grade model: v = v_flat * exp(-k * grade) uphill, v = v_flat * (1 + b * |grade|)
    # downhill, capped. k is chosen so a 5% climb roughly halves a mechanical bike's
    # speed (exp(-14 * 0.05) = 0.50) -- the widely quoted rule of thumb -- while a
    # 250 W pedal-assist motor keeps ~78% of flat speed on the same climb.
    # See docs/cost-model.md for the derivation and the numbers it produces.
    grade_penalty_mechanical: float = 14.0
    grade_penalty_electric: float = 5.0
    descent_bonus: float = 6.0  # +6% speed per 1% of downhill grade ...
    max_descent_speed_kmh: float = 28.0  # ... until braking / comfort caps it
    max_abs_grade: float = 0.30  # steeper than this is a data error, not a road

    # Straight-line distance underestimates street distance. Used only when a pair
    # is missing from the OSRM matrix. MEASURED 2026-09-19 over 49,626 station
    # pairs on the bicycle network (`scripts/calibrate_detour.py`): median 1.267,
    # mean 1.286, p10-p90 1.13-1.44. The original guess was 1.35.
    detour_factor: float = 1.27
    # Walkers cut corners cyclists cannot (parks, stairs, against one-way streets).
    walk_detour_factor: float = 1.20

    # Traffic lights: estimated as (distance / spacing) * delay.
    traffic_light_spacing_m: float = 250.0
    traffic_light_delay_seconds: float = 12.0

    # Fixed cost of a clock reset at a transfer station: dock the bike, wait for
    # the ride to close, unlock the same bike again. Also charged on the final
    # leg, where it slightly overstates (dock only) -- kept uniform so the numbers
    # the search optimises are the numbers it reports.
    docking_overhead_seconds: float = 60.0

    # Walking, for the first/last leg between a real address and a station.
    walking_speed_kmh: float = 4.8
    max_walk_meters: float = 800.0
    # When no usable station lies within max_walk_meters (empty stations at night,
    # a suburb edge), fall back to the single nearest one up to this far rather than
    # refusing to plan. The itinerary shows the long walk; the rider decides.
    max_walk_fallback_meters: float = 2000.0

    # --- Elevation / hill penalty ---------------------------------------------
    # Québec City's Basse-Ville/Haute-Ville escarpment is the reason this project
    # is interesting. A flat-earth model will badly underestimate climbs.
    enable_elevation_model: bool = True

    # --- Billing ---------------------------------------------------------------
    # PBSC systems bill overage per started minute. Rounding up is the conservative
    # assumption; flip this if the operator's receipt shows otherwise.
    bill_partial_minutes_as_whole: bool = True

    # --- Risk model ------------------------------------------------------------
    # A segment estimated at exactly the limit is a coin flip. Treat the estimate
    # as the mean of a distribution and reserve headroom.
    safety_buffer_seconds: float = 90.0
    risk_low_threshold: float = 0.75  # segment uses < 75% of limit -> LOW
    risk_high_threshold: float = 0.95  # segment uses > 95% of limit -> HIGH

    # --- Graph construction ----------------------------------------------------
    # Connecting all 226 stations to each other is 50k directed edges. Pruning is
    # by TIME, not by nearest-neighbour rank: in the dense core the 25 nearest
    # stations are all within 1.5 km, and a rank cap would drop exactly the long,
    # useful hops that keep transfer counts low. An edge survives if its estimated
    # duration is under `max_edge_limit_multiple` x the ride limit (beyond 1.5x the
    # overage alone is > $4.50 and never on a sensible frontier), within a generous
    # straight-line radius, with a high rank cap as a safety valve.
    max_edge_distance_km: float = 12.0
    max_edge_limit_multiple: float = 1.5
    max_neighbours_per_station: int = 250

    # A station with 1 bike may be empty by the time you arrive. Require a margin.
    min_bikes_required: int = 1
    min_docks_required: int = 1

    # --- Planner ---------------------------------------------------------------
    # How many nearby stations to consider as the first / last station of a trip.
    # Each candidate adds a handful of walk edges; the search is unaffected otherwise.
    walk_candidates: int = 5

    # --- Geocoding (place search for the web UI) --------------------------------
    # Google Places (New) when a key is configured -- proxied server-side so the
    # browser never talks to Google directly and the key's restrictions apply here.
    # Photon (komoot), an OSM geocoder that permits autocomplete-style queries, is
    # the fallback and handles reverse geocoding. Both are clipped to Québec City.
    google_maps_api_key: str = ""
    google_places_url: str = "https://places.googleapis.com/v1"
    geocoder_url: str = "https://photon.komoot.io/api/"
    geocoder_bbox: str = "-71.60,46.65,-70.95,47.00"  # min_lon,min_lat,max_lon,max_lat
    geocoder_bias_lat: float = 46.8139
    geocoder_bias_lon: float = -71.2080

    # --- API -------------------------------------------------------------------
    # Comma-separated origins allowed to call the API from a browser ("*" = any).
    cors_origins: str = "*"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor. Import this, never construct `Settings()` directly.

    The cache means config is parsed once per process, and every module sees the
    same object. In tests you can clear it with `get_settings.cache_clear()`.
    """
    return Settings()
