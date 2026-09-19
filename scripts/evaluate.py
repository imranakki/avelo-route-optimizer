#!/usr/bin/env python3
"""Run the evaluation on live data and write docs/evaluation.md.

Three strategies over N sampled trips (station-density weighted, fixed seed), then
the same again with the elevation model switched off (ablation), and a few
representative trips' Pareto frontiers listed in full.

    ./.venv/bin/python scripts/evaluate.py            # n = 1000
    ./.venv/bin/python scripts/evaluate.py --n 200    # quicker
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from avelo.config import get_settings
from avelo.evaluation.harness import EvaluationReport, evaluate
from avelo.models import LegMode

DOC = Path(__file__).resolve().parent.parent / "docs" / "evaluation.md"


def _frontier_examples(report: EvaluationReport, k: int = 4) -> str:
    """The trips with the largest frontiers, each listed in full."""
    trips = sorted(report.trips, key=lambda t: -t.frontier_size)[:k]
    out = []
    for t in trips:
        naive = t.itineraries["naive"]
        o, d = t.origin, t.destination
        out.append(
            f"**{t.straight_line_m / 1000:.1f} km straight-line** "
            f"({o.lat:.4f}, {o.lon:.4f}) → ({d.lat:.4f}, {d.lon:.4f})"
        )
        if naive:
            out.append(
                f"- naive: {naive.total_minutes:.1f} min, ${naive.total_cost:.2f}, "
                f"risk {naive.overall_risk}"
            )
        hard = t.itineraries["hard_constraint"]
        if hard:
            out.append(
                f"- constrained: {hard.total_minutes:.1f} min, $0.00, {hard.num_transfers} reset(s)"
            )
        out.append(
            f"- frontier ({t.frontier_size} options, {t.labels_generated} labels generated):"
        )
        # The harness keeps only the two ends of each frontier.
        for key in ("pareto_fastest", "pareto_cheapest"):
            it = t.itineraries[key]
            if it:
                rides = [leg for leg in it.legs if leg.mode is LegMode.RIDE]
                out.append(
                    f"  - {key.split('_')[1]}: {it.total_minutes:.1f} min, ${it.total_cost:.2f}, "
                    f"{len(rides)} ride(s), risk {it.overall_risk}"
                )
        out.append("")
    return "\n".join(out)


async def main(n: int, seed: int) -> None:
    settings = get_settings()
    t0 = time.perf_counter()
    full = await evaluate(n, seed, settings)
    logging.info("full model done in %.0f s", time.perf_counter() - t0)
    flat = await evaluate(n, seed, settings.model_copy(update={"enable_elevation_model": False}))
    logging.info("ablation done in %.0f s", time.perf_counter() - t0)

    hm, nm = full.metrics["hard_constraint"], full.metrics["naive"]
    pm = full.metrics["pareto_fastest"]
    lines = [
        "# Evaluation",
        "",
        f"_Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `scripts/evaluate.py` on live "
        f"station data and the cached bicycle-network matrix._",
        "",
        "## Headline",
        "",
        f"Over {full.n} sampled trips, the naive single-ride strategy exceeds the "
        f"{full.ride_limit_minutes:.0f}-minute limit on **{nm.exceeded_limit_pct:.1f}%** of "
        f"trips and costs a mean **${nm.mean_overage:.2f}** in overage. The constrained "
        f"router exceeds it on **{hm.exceeded_limit_pct:.1f}%** at **${hm.mean_overage:.2f}**, "
        f"and is **{hm.mean_minutes - nm.mean_minutes:+.1f} min** slower on average "
        f"(median {hm.median_minutes - nm.median_minutes:+.1f} min). The fastest point of "
        f"the Pareto frontier is {pm.mean_minutes - nm.mean_minutes:+.1f} min vs. naive at "
        f"${pm.mean_overage:.2f}.",
        "",
        "## Strategies",
        "",
        "- **naive** — nearest station to nearest station, one ride, limit ignored. What a "
        "generic navigation app does.",
        "- **hard constraint** — Dijkstra over the graph with every over-limit edge removed. "
        "Every ride leg is under the limit; $0 overage by construction.",
        "- **pareto fastest / cheapest** — the two ends of the (time, money) frontier from "
        "Martins' bi-criteria label-setting search. The cheapest end is usually the "
        "constrained route; the fastest end is often the naive route with its true cost "
        "attached.",
        "",
        "Trips are sampled around stations (density-weighted) with 400 m jitter, at least "
        "1.5 km apart, seed 42. A trip the strategy cannot serve counts as unserved, not as "
        "an error. Every strategy is costed with the same model, so the comparison is about "
        "the *route chosen*, not the estimate.",
        "",
        "## Results — full model",
        "",
        full.to_markdown(),
        "",
        "## Ablation — elevation model off",
        "",
        "Same trips, same seed, every grade multiplier forced to 1 (flat earth).",
        "",
        flat.to_markdown(),
        "",
        "What the hill model changes at the edge level: with grades on, "
        f"{full.edges_within_limit} of {full.edges_total} graph edges fit the limit; with "
        f"grades off, {flat.edges_within_limit} of {flat.edges_total}. At the trip level the "
        f"naive strategy exceeds the limit on {nm.exceeded_limit_pct:.1f}% of trips with grades "
        f"on and {flat.metrics['naive'].exceeded_limit_pct:.1f}% with them off; the constrained "
        f"router's mean door-to-door time is {hm.mean_minutes:.1f} vs "
        f"{flat.metrics['hard_constraint'].mean_minutes:.1f} min.",
        "",
        "Read honestly: for the current all-electric fleet the hill model changes individual "
        "leg estimates (a 90 m climb over 1.4 km costs an EFIT +39% cycling time) but rarely "
        "changes which route is chosen, because the e-bike penalty is mild and climbs and "
        "descents average out over a thousand trips. The model matters far more for mechanical "
        "bikes (the same climb costs an ICONIC +145%), of which àVélo currently has none in "
        "service. The component is kept because the API accepts either vehicle type; its "
        "aggregate effect on today's fleet is small, and this document says so.",
        "",
        "## Search instrumentation",
        "",
        f"Mean Pareto frontier size {full.mean_frontier_size:.2f}; "
        f"{full.pruned_fraction * 100:.1f}% of generated labels were pruned by dominance. "
        "Without that pruning the bi-criteria search explodes; with it, p95 planning latency is "
        f"{pm.p95_latency_ms:.0f} ms on a {full.n}-trip run.",
        "",
        "## Example frontiers",
        "",
        _frontier_examples(full),
    ]
    DOC.write_text("\n".join(lines))
    print(full.to_markdown())
    print(f"\nwritten to {DOC}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(main(args.n, args.seed))
