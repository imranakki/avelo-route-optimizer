"use client";

import { km, minutes, money, type CompareResponse, type Itinerary, type Risk } from "@/lib/api";

export type Choice = { key: string; itinerary: Itinerary; color: string; title: string; tag: string };

const RISK_CLASS: Record<Risk, string> = {
  LOW: "text-hard",
  MEDIUM: "text-warn",
  HIGH: "text-danger",
  INFEASIBLE: "text-danger",
};

const RISK_TEXT: Record<Risk, string> = {
  LOW: "low risk",
  MEDIUM: "medium risk",
  HIGH: "high risk",
  INFEASIBLE: "over the limit",
};

const same = (a: Itinerary | null, b: Itinerary) =>
  !!a && Math.abs(a.total_seconds - b.total_seconds) < 0.5 && a.total_cost === b.total_cost;

export function choicesFrom(data: CompareResponse): Choice[] {
  const out: Choice[] = [];
  if (data.hard_constraint) {
    out.push({ key: "hard", itinerary: data.hard_constraint, color: "var(--hard)", title: "Every leg under the limit", tag: "$0" });
  }
  let naiveShown = false;
  data.options.forEach((o, i) => {
    // The constrained route usually reappears as the cheapest frontier point, and the
    // naive ride often as the fastest; show each itinerary once.
    if (same(data.hard_constraint, o)) return;
    const isNaive = same(data.naive, o);
    naiveShown ||= isNaive;
    const tag = i === 0 ? "fastest" : i === data.options.length - 1 ? "cheapest" : "trade-off";
    out.push({
      key: `p${i}`,
      itinerary: o,
      color: isNaive ? "var(--naive)" : "var(--pareto)",
      title: isNaive ? "Direct ride (what a nav app suggests)" : `Frontier option ${i + 1}`,
      tag: isNaive ? `${tag} · naive` : tag,
    });
  });
  if (data.naive && !naiveShown && !same(data.hard_constraint, data.naive)) {
    out.push({ key: "naive", itinerary: data.naive, color: "var(--naive)", title: "Naive: one ride, nearest stations", tag: "baseline" });
  }
  return out;
}

function Card({ choice, selected, onSelect }: { choice: Choice; selected: boolean; onSelect: () => void }) {
  const it = choice.itinerary;
  const rides = it.legs.filter((l) => l.mode === "RIDE");
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={`w-full rounded-xl border bg-panel p-3 text-left transition-shadow ${
        selected ? "border-transparent shadow-[0_0_0_2px_var(--ring)]" : "border-line hover:border-muted/60"
      }`}
      style={{ ["--ring" as string]: choice.color }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: choice.color }} />
            <span className="truncate text-[13px] font-medium">{choice.title}</span>
          </div>
          <div className="tnum mt-1 flex flex-wrap items-baseline gap-x-2 text-[15px]">
            <span className="font-semibold">{minutes(it.total_seconds)}</span>
            <span className={it.total_cost > 0 ? "font-semibold text-danger" : "font-semibold text-hard"}>{money(it.total_cost)}</span>
            <span className="text-xs text-muted">
              {km(it.total_distance_m)} · {it.num_transfers} reset{it.num_transfers === 1 ? "" : "s"} ·{" "}
              <span className={RISK_CLASS[it.overall_risk]}>{RISK_TEXT[it.overall_risk]}</span>
            </span>
          </div>
        </div>
        <span className="shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.05em] text-white" style={{ background: choice.color }}>
          {choice.tag}
        </span>
      </div>

      {selected && (
        <ol className="mt-3 space-y-1.5 border-t border-line pt-2 text-[13px]">
          {it.legs.map((leg, i) => {
            const isReset = leg.mode === "RIDE" && it.legs[i + 1]?.mode === "RIDE";
            return (
              <li key={i} className="flex gap-2">
                <span className="w-5 shrink-0 text-center" aria-hidden>
                  {leg.mode === "WALK" ? "🚶" : "🚲"}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="tnum">
                    {leg.mode === "WALK" ? "Walk" : "Ride"} {minutes(leg.duration_seconds)} · {km(leg.distance_m)}
                  </span>
                  {leg.mode === "RIDE" && (
                    <span className="text-muted">
                      {leg.elevation_gain_m > 0 ? ` · +${Math.round(leg.elevation_gain_m)} m` : ""} ·{" "}
                      <span className={RISK_CLASS[leg.risk]}>{RISK_TEXT[leg.risk]}</span>
                      {leg.overage_cost > 0 ? <span className="text-danger"> · {money(leg.overage_cost)}</span> : null}
                    </span>
                  )}
                  <span className="block truncate text-muted">
                    → {leg.to_name}
                    {isReset && <span className="ml-1 rounded bg-panel-2 px-1 text-[11px] font-medium text-ink">reset</span>}
                  </span>
                </span>
              </li>
            );
          })}
          {rides.length > 0 && (
            <li className="pt-1 text-xs text-muted">
              Riding {minutes(rides.reduce((a, l) => a + l.cycling_seconds, 0))}, lights {minutes(rides.reduce((a, l) => a + l.traffic_light_seconds, 0))},
              docking {minutes(rides.reduce((a, l) => a + l.docking_seconds, 0))}.
            </li>
          )}
        </ol>
      )}
    </button>
  );
}

export default function ResultsPanel({
  data,
  choices,
  selectedKey,
  onSelect,
}: {
  data: CompareResponse;
  choices: Choice[];
  selectedKey: string | null;
  onSelect: (key: string) => void;
}) {
  const naive = data.naive;
  const hard = data.hard_constraint;
  const saving = naive && hard ? naive.total_cost - hard.total_cost : 0;
  const slower = naive && hard ? (hard.total_seconds - naive.total_seconds) / 60 : 0;

  return (
    <section className="space-y-2">
      {naive && hard && (
        <p className="rounded-lg bg-panel-2 px-3 py-2 text-[13px]">
          {saving > 0 ? (
            <>
              Resetting the clock saves <b className="text-hard">{money(saving)}</b> for{" "}
              <b className="tnum">{slower >= 0 ? `+${slower.toFixed(1)}` : slower.toFixed(1)} min</b> versus riding straight through.
            </>
          ) : (
            <>The direct ride fits the limit: no reset needed, nothing to pay.</>
          )}
        </p>
      )}
      {choices.map((c) => (
        <Card key={c.key} choice={c} selected={c.key === selectedKey} onSelect={() => onSelect(c.key)} />
      ))}
      <p className="tnum pt-1 text-[11px] leading-relaxed text-muted">
        Pareto search: {data.stats.labels_generated.toLocaleString()} labels, {(data.stats.pruned_fraction * 100).toFixed(1)}% pruned by dominance,
        frontier {data.stats.frontier_size}, {data.stats.planning_ms} ms. Walk legs {data.stats.walk_legs_street_routed ? "street-routed" : "estimated"}.
      </p>
    </section>
  );
}
