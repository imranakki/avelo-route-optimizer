#!/usr/bin/env bash
# Build local OSRM routing data for Québec City: bicycle and foot profiles.
#
# Why self-host: the public OSRM servers are run by volunteers for light use.
# Building the full station matrix and 28k route polylines is not light use, and
# the app should not depend on someone else's rate limits in production. A local
# OSRM answers in ~1 ms with no quota.
#
# Steps (all via Docker, nothing installed on the host):
#   1. download the Québec province extract from Geofabrik (~1.2 GB, once)
#   2. clip it to the Québec City area with osmium (~25 MB)
#   3. run osrm-extract / partition / customize for each profile
#
# Output: data/osrm/{bicycle,foot}/quebec-city.osrm*  (used by docker-compose.yml)
#
#   ./scripts/prepare_osrm.sh            # everything
#   KEEP_PBF=1 ./scripts/prepare_osrm.sh # keep the 1.2 GB province file afterwards

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="$ROOT/data/osrm"
PROVINCE_URL="https://download.geofabrik.de/north-america/canada/quebec-latest.osm.pbf"
# Generous box around the àVélo service area (stations span 46.75-46.89 N, 71.37-71.14 W).
BBOX="-71.55,46.65,-71.00,47.00"
OSRM_IMAGE="ghcr.io/project-osrm/osrm-backend:latest"
OSMIUM_IMAGE="stefda/osmium-tool:latest"

mkdir -p "$DATA"

if [ ! -f "$DATA/quebec-city.osm.pbf" ]; then
  if [ ! -f "$DATA/quebec-latest.osm.pbf" ]; then
    echo ">> downloading Québec extract (~1.2 GB)"
    curl -L --fail --progress-bar -o "$DATA/quebec-latest.osm.pbf.part" "$PROVINCE_URL"
    mv "$DATA/quebec-latest.osm.pbf.part" "$DATA/quebec-latest.osm.pbf"
  fi
  echo ">> clipping to Québec City ($BBOX)"
  docker run --rm -v "$DATA:/data" "$OSMIUM_IMAGE" \
    osmium extract --bbox "$BBOX" --strategy complete_ways --overwrite \
    -o /data/quebec-city.osm.pbf /data/quebec-latest.osm.pbf
  if [ -z "${KEEP_PBF:-}" ]; then
    rm -f "$DATA/quebec-latest.osm.pbf"
  fi
fi
ls -lh "$DATA/quebec-city.osm.pbf"

for profile in bicycle foot; do
  dir="$DATA/$profile"
  if [ -f "$dir/quebec-city.osrm.mldgr" ]; then
    echo ">> $profile: already prepared, skipping"
    continue
  fi
  echo ">> $profile: extract / partition / customize"
  mkdir -p "$dir"
  cp "$DATA/quebec-city.osm.pbf" "$dir/"
  docker run --rm -v "$dir:/data" "$OSRM_IMAGE" osrm-extract -p "/opt/$profile.lua" /data/quebec-city.osm.pbf
  docker run --rm -v "$dir:/data" "$OSRM_IMAGE" osrm-partition /data/quebec-city.osrm
  docker run --rm -v "$dir:/data" "$OSRM_IMAGE" osrm-customize /data/quebec-city.osrm
  rm -f "$dir/quebec-city.osm.pbf"
done

echo ">> done. Start the routers with:  docker compose up -d osrm-bike osrm-foot"
