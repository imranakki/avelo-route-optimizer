# The cost model

How long does it take to ride from station A to station B? Every routing decision
in this project rests on that estimate, so this document states the model, its
parameters, and the reasoning behind each value. All parameters live in
`src/avelo/config.py` and can be overridden with `AVELO_*` environment variables.

```
ride_seconds = cycling + traffic_lights + reset_overhead + safety_buffer
```

## Distance

Station-to-station distances are **real bicycle-network distances** from OSRM
(`/table` over all 225 stations, 50,400 ordered pairs, cached on disk forever).
They are asymmetric — one-way streets and the Côte de la Montagne stairs mean
A→B ≠ B→A — and the graph keeps them that way.

The fallback for any pair missing from the matrix, and the estimate for walking
legs when the foot router is unavailable, is `haversine × detour_factor`
(1.27 for cycling, 1.20 for walking). The cycling factor is **measured**, not
guessed: over 49,626 station pairs the bicycle-network distance is a median
1.267× the straight line (mean 1.286, p10–p90 1.13–1.44). The project's original
guess was 1.35; a single spot-check had suggested 1.21; both were wrong.
`scripts/calibrate_detour.py` reproduces the number.

## Speed and grade

Flat cruising speed comes from config: **17 km/h** for an electric EFIT/BOOST,
**13 km/h** for a mechanical ICONIC/FIT. These are shared-city-bike speeds, not
road-bike speeds; a 2 km flat ride is ~7 min of pure pedalling on an EFIT.

Grade is `elevation_change / distance`, signed, clamped to ±30 % (anything
steeper is a data error, not a road). The speed multiplier is:

| direction | multiplier | parameter |
|---|---|---|
| uphill (`g ≥ 0`) | `exp(−k · g)` | `k = 14` mechanical, `k = 5` electric |
| downhill (`g < 0`) | `min(1 + 6·|g|, v_max / v_flat)` | `v_max = 28 km/h` |

**Why exponential decay uphill.** It is the first-order form used by Tobler's
hiking function and by cycling power models when speed is well below the
aerodynamic regime, which it is at 13 km/h. `k = 14` was chosen so that a 5 %
grade halves a mechanical bike's speed (`exp(−0.7) ≈ 0.50`), the commonly cited
rule of thumb for an untrained rider on a 25 kg bike. `k = 5` gives a 250 W
pedal-assist ~78 % of its flat speed on the same grade, consistent with e-bike
riders reporting that moderate hills "mostly disappear".

**Why the descent is capped and linear.** Gravity helps, but riders brake, and a
shared city bike is not a vehicle anyone takes to 40 km/h. The asymmetry is
deliberate and load-bearing: a model that is linear in signed grade lets +5 %
and −5 % cancel, so every hilly round trip is underestimated. Here the time lost
climbing is only partially recovered descending — see the round-trip row below.

What the model produces per kilometre:

| grade | EFIT | ICONIC |
|---|---|---|
| −10% | 27.2 km/h | 20.8 km/h |
| −6% | 23.1 km/h | 17.7 km/h |
| −3% | 20.1 km/h | 15.3 km/h |
| 0% | 17.0 km/h | 13.0 km/h |
| +2% | 15.4 km/h | 9.8 km/h |
| +4% | 13.9 km/h | 7.4 km/h |
| +6% | 12.6 km/h | 5.6 km/h |
| +8% | 11.4 km/h | 4.2 km/h |
| +10% | 10.3 km/h | 3.2 km/h |
| +12% | 9.3 km/h | 2.4 km/h |

An ICONIC on an 8 % grade is at walking pace — which is what people actually do
on the Côte de la Montagne: get off and push.

The Basse-Ville → Haute-Ville escarpment in miniature (1.4 km of street, 90 m of
climb, ~6.4 % average):

| | up | down | flat | round trip | 2 × flat |
|---|---|---|---|---|---|
| EFIT | 6.8 min | 3.6 min | 4.9 min | 10.4 min | 9.9 min |
| ICONIC | 15.9 min | 4.7 min | 6.5 min | 20.6 min | 12.9 min |

`AVELO_ENABLE_ELEVATION_MODEL=false` sets every multiplier to 1 — the ablation
used in `docs/evaluation.md`.

## Traffic lights

`expected_lights = distance / 250 m`, times **12 s** per light. Where 12 s comes
from: arriving at a uniformly random moment in a cycle of length *C* with red
fraction *r*, you wait with probability *r*, and the remaining red is uniform on
[0, *rC*], so `E[wait] = r · rC / 2`. For *C* = 60 s, *r* = 0.5 that is 7.5 s;
add ~5 s to stop and re-accelerate a heavy bike. The *expectation* is what
belongs in a routing cost, not the worst case.

## Reset overhead and safety buffer

The rider keeps the same bike for the whole trip; a "transfer" is docking, letting
the ride close, and unlocking again. That costs **60 s** per ride leg (applied
uniformly, including the final leg where it slightly overstates — costing every
leg identically keeps the numbers the search optimises equal to the numbers it
reports).

A **90 s safety buffer** is added to every leg. The estimate is a point
prediction; the real duration is a distribution around it. The buffer shifts the
mean so that "under the limit" means "under the limit with margin", and the risk
thresholds (75 % → MEDIUM, 95 % → HIGH) describe how much of that distribution
is likely past 100 %.

## Money

Beyond the plan's limit (30 or 45 min), àVélo charges **$0.30 per minute**,
verified from the live `system_pricing_plans` feed. Partial minutes are billed as
whole minutes (config `bill_partial_minutes_as_whole`), the conservative reading
of PBSC receipts; 10 min 1 s over costs $3.30, not $3.01.
