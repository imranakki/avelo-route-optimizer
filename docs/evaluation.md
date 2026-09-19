# Evaluation

_Generated 2026-09-19 20:34 UTC by `scripts/evaluate.py` on live station data and the cached bicycle-network matrix._

## Headline

Over 1000 sampled trips, the naive single-ride strategy exceeds the 30-minute limit on **49.6%** of trips and costs a mean **$2.74** in overage. The constrained router exceeds it on **0.0%** at **$0.00**, and is **+1.8 min** slower on average (median +0.2 min). The fastest point of the Pareto frontier is +0.2 min vs. naive at $1.26.

## Strategies

- **naive** — nearest station to nearest station, one ride, limit ignored. What a generic navigation app does.
- **hard constraint** — Dijkstra over the graph with every over-limit edge removed. Every ride leg is under the limit; $0 overage by construction.
- **pareto fastest / cheapest** — the two ends of the (time, money) frontier from Martins' bi-criteria label-setting search. The cheapest end is usually the constrained route; the fastest end is often the naive route with its true cost attached.

Trips are sampled around stations (density-weighted) with 400 m jitter, at least 1.5 km apart, seed 42. A trip the strategy cannot serve counts as unserved, not as an error. Every strategy is costed with the same model, so the comparison is about the *route chosen*, not the estimate.

## Results — full model

| metric | naive | hard constraint | pareto fastest | pareto cheapest |
|---|---:|---:|---:|---:|
| trips served | 1000/1000 | 1000/1000 | 1000/1000 | 1000/1000 |
| trips exceeding the limit | 49.6% | 0.0% | 45.1% | 0.0% |
| mean overage charge per trip | $2.74 | $0.00 | $1.26 | $0.00 |
| mean door-to-door time | 40.0 min | 41.9 min | 40.2 min | 41.4 min |
| median door-to-door time | 36.3 min | 36.5 min | 35.6 min | 35.9 min |
| p95 door-to-door time | 72.6 min | 82.6 min | 74.9 min | 78.9 min |
| median transfers | 0 | 0 | 0 | 0 |
| p95 planning latency | 0.6 ms | 93.9 ms | 237.3 ms | 237.3 ms |

_n = 1000 trips, seed = 42, limit = 30 min, elevation model = on, 100% of edges street-routed. Pareto search: mean frontier size 2.30, 98.5% of generated labels pruned by dominance._

## Ablation — elevation model off

Same trips, same seed, every grade multiplier forced to 1 (flat earth).

| metric | naive | hard constraint | pareto fastest | pareto cheapest |
|---|---:|---:|---:|---:|
| trips served | 1000/1000 | 1000/1000 | 1000/1000 | 1000/1000 |
| trips exceeding the limit | 49.9% | 0.0% | 45.9% | 0.0% |
| mean overage charge per trip | $2.75 | $0.00 | $1.26 | $0.00 |
| mean door-to-door time | 40.1 min | 41.8 min | 40.3 min | 41.5 min |
| median door-to-door time | 36.6 min | 36.7 min | 35.5 min | 35.9 min |
| p95 door-to-door time | 72.9 min | 80.4 min | 75.0 min | 79.1 min |
| median transfers | 0 | 0 | 0 | 0 |
| p95 planning latency | 0.6 ms | 114.1 ms | 256.2 ms | 256.2 ms |

_n = 1000 trips, seed = 42, limit = 30 min, elevation model = off, 100% of edges street-routed. Pareto search: mean frontier size 2.31, 98.5% of generated labels pruned by dominance._

What the hill model changes at the edge level: with grades on, 27759 of 38839 graph edges fit the limit; with grades off, 28051 of 39194. At the trip level the naive strategy exceeds the limit on 49.6% of trips with grades on and 49.9% with them off; the constrained router's mean door-to-door time is 41.9 vs 41.8 min.

Read honestly: for the current all-electric fleet the hill model changes individual leg estimates (a 90 m climb over 1.4 km costs an EFIT +39% cycling time) but rarely changes which route is chosen, because the e-bike penalty is mild and climbs and descents average out over a thousand trips. The model matters far more for mechanical bikes (the same climb costs an ICONIC +145%), of which àVélo currently has none in service. The component is kept because the API accepts either vehicle type; its aggregate effect on today's fleet is small, and this document says so.

## Search instrumentation

Mean Pareto frontier size 2.30; 98.5% of generated labels were pruned by dominance. Without that pruning the bi-criteria search explodes; with it, p95 planning latency is 237 ms on a 1000-trip run.

## Example frontiers

**16.9 km straight-line** (46.8913, -71.1535) → (46.8458, -71.3656)
- naive: 95.9 min, $18.00, risk INFEASIBLE
- constrained: 112.7 min, $0.00, 4 reset(s)
- frontier (14 options, 182138 labels generated):
  - fastest: 101.2 min, $9.00, 3 ride(s), risk INFEASIBLE
  - cheapest: 112.7 min, $0.00, 5 ride(s), risk MEDIUM

**21.5 km straight-line** (46.7598, -71.3506) → (46.8952, -71.1481)
- naive: 128.8 min, $23.70, risk INFEASIBLE
- constrained: 149.2 min, $0.00, 5 reset(s)
- frontier (12 options, 200404 labels generated):
  - fastest: 134.9 min, $7.80, 3 ride(s), risk INFEASIBLE
  - cheapest: 145.4 min, $0.00, 5 ride(s), risk HIGH

**19.5 km straight-line** (46.7598, -71.3208) → (46.8929, -71.1541)
- naive: 109.7 min, $18.90, risk INFEASIBLE
- constrained: 116.9 min, $0.00, 3 reset(s)
- frontier (12 options, 109530 labels generated):
  - fastest: 114.0 min, $8.40, 3 ride(s), risk INFEASIBLE
  - cheapest: 116.8 min, $0.00, 4 ride(s), risk HIGH

**8.6 km straight-line** (46.8552, -71.3661) → (46.8678, -71.2542)
- naive: 56.6 min, $4.80, risk INFEASIBLE
- constrained: 74.2 min, $0.00, 2 reset(s)
- frontier (11 options, 232846 labels generated):
  - fastest: 59.2 min, $4.20, 1 ride(s), risk INFEASIBLE
  - cheapest: 72.2 min, $0.00, 3 ride(s), risk HIGH
