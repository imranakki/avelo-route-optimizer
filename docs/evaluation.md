# Evaluation

_Generated 2026-09-19 01:46 UTC by `scripts/evaluate.py` on live station data and the cached bicycle-network matrix._

## Headline

Over 1000 sampled trips, the naive single-ride strategy exceeds the 30-minute limit on **49.9%** of trips and costs a mean **$2.75** in overage. The constrained router exceeds it on **0.0%** at **$0.00**, and is **+1.4 min** slower on average (median -0.4 min). The fastest point of the Pareto frontier is +0.2 min vs. naive at $1.25.

## Strategies

- **naive** — nearest station to nearest station, one ride, limit ignored. What a generic navigation app does.
- **hard constraint** — Dijkstra over the graph with every over-limit edge removed. Every ride leg is under the limit; $0 overage by construction.
- **pareto fastest / cheapest** — the two ends of the (time, money) frontier from Martins' bi-criteria label-setting search. The cheapest end is usually the constrained route; the fastest end is often the naive route with its true cost attached.

Trips are sampled around stations (density-weighted) with 400 m jitter, at least 1.5 km apart, seed 42. A trip the strategy cannot serve counts as unserved, not as an error. Every strategy is costed with the same model, so the comparison is about the *route chosen*, not the estimate.

## Results — full model

| metric | naive | hard constraint | pareto fastest | pareto cheapest |
|---|---:|---:|---:|---:|
| trips served | 997/1000 | 997/1000 | 997/1000 | 997/1000 |
| trips exceeding the limit | 49.9% | 0.0% | 45.1% | 0.0% |
| mean overage charge per trip | $2.75 | $0.00 | $1.25 | $0.00 |
| mean door-to-door time | 39.9 min | 41.4 min | 40.1 min | 41.4 min |
| median door-to-door time | 36.4 min | 36.0 min | 35.8 min | 36.0 min |
| p95 door-to-door time | 71.9 min | 78.5 min | 74.2 min | 78.5 min |
| median transfers | 0 | 0 | 0 | 0 |
| p95 planning latency | 0.6 ms | 89.0 ms | 232.7 ms | 232.7 ms |

_n = 1000 trips, seed = 42, limit = 30 min, elevation model = on, 100% of edges street-routed. Pareto search: mean frontier size 2.30, 98.5% of generated labels pruned by dominance._

## Ablation — elevation model off

Same trips, same seed, every grade multiplier forced to 1 (flat earth).

| metric | naive | hard constraint | pareto fastest | pareto cheapest |
|---|---:|---:|---:|---:|
| trips served | 994/1000 | 994/1000 | 994/1000 | 994/1000 |
| trips exceeding the limit | 50.2% | 0.0% | 45.5% | 0.0% |
| mean overage charge per trip | $2.76 | $0.00 | $1.25 | $0.00 |
| mean door-to-door time | 40.0 min | 41.4 min | 40.2 min | 41.4 min |
| median door-to-door time | 36.6 min | 36.1 min | 35.8 min | 36.1 min |
| p95 door-to-door time | 71.8 min | 77.8 min | 74.2 min | 77.8 min |
| median transfers | 0 | 0 | 0 | 0 |
| p95 planning latency | 0.6 ms | 116.6 ms | 245.3 ms | 245.3 ms |

_n = 1000 trips, seed = 42, limit = 30 min, elevation model = off, 100% of edges street-routed. Pareto search: mean frontier size 2.29, 98.5% of generated labels pruned by dominance._

What the hill model changes at the edge level: with grades on, 27603 of 38704 graph edges fit the limit; with grades off, 27738 of 38872. At the trip level the naive strategy exceeds the limit on 49.9% of trips with grades on and 50.2% with them off; the constrained router's mean door-to-door time is 41.4 vs 41.4 min.

Read honestly: for the current all-electric fleet the hill model changes individual leg estimates (a 90 m climb over 1.4 km costs an EFIT +38% cycling time) but rarely changes which route is chosen, because the e-bike penalty is mild and climbs and descents average out over a thousand trips. The model matters far more for mechanical bikes (the same climb costs an ICONIC +145%), of which àVélo currently has none in service. The component is kept because the API accepts either vehicle type; its aggregate effect on today's fleet is small, and this document says so.

## Search instrumentation

Mean Pareto frontier size 2.30; 98.5% of generated labels were pruned by dominance. Without that pruning the bi-criteria search explodes; with it, p95 planning latency is 233 ms on a 1000-trip run.

## Example frontiers

**16.9 km straight-line** (46.8913, -71.1535) → (46.8458, -71.3656)
- naive: 95.9 min, $18.00, risk INFEASIBLE
- constrained: 112.3 min, $0.00, 4 reset(s)
- frontier (15 options, 176401 labels generated):
  - fastest: 101.2 min, $9.00, 3 ride(s), risk INFEASIBLE
  - cheapest: 112.3 min, $0.00, 5 ride(s), risk HIGH

**21.5 km straight-line** (46.7598, -71.3506) → (46.8952, -71.1481)
- naive: 120.2 min, $24.60, risk INFEASIBLE
- constrained: 137.3 min, $0.00, 5 reset(s)
- frontier (13 options, 197338 labels generated):
  - fastest: 126.3 min, $8.70, 3 ride(s), risk INFEASIBLE
  - cheapest: 137.3 min, $0.00, 6 ride(s), risk HIGH

**8.6 km straight-line** (46.8552, -71.3661) → (46.8678, -71.2542)
- naive: 56.6 min, $4.80, risk INFEASIBLE
- constrained: 72.2 min, $0.00, 2 reset(s)
- frontier (11 options, 230835 labels generated):
  - fastest: 59.2 min, $4.20, 1 ride(s), risk INFEASIBLE
  - cheapest: 72.2 min, $0.00, 3 ride(s), risk HIGH

**11.9 km straight-line** (46.8441, -71.2120) → (46.8514, -71.3681)
- naive: 66.3 min, $9.60, risk INFEASIBLE
- constrained: 82.9 min, $0.00, 3 reset(s)
- frontier (10 options, 77083 labels generated):
  - fastest: 72.1 min, $3.90, 2 ride(s), risk INFEASIBLE
  - cheapest: 82.9 min, $0.00, 4 ride(s), risk HIGH
