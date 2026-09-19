"use client";

import { clock, km, minutes, money, type Itinerary, type Risk, type TripResponse } from "@/lib/api";
import FrontierChart from "./FrontierChart";
import { legColorsFor, legSegments } from "./mapProps";
import ShareRow from "./ShareRow";

export type Choice = { key: string; itinerary: Itinerary; color: string; title: string; kind: "limit" | "frontier" | "naive" };

const RISK_TEXT: Record<Risk, string> = {
  LOW: "low risk",
  MEDIUM: "medium risk",
  HIGH: "high risk",
  INFEASIBLE: "over the limit",
};
const RISK_CLASS: Record<Risk, string> = {
  LOW: "text-limit",
  MEDIUM: "text-warn",
  HIGH: "text-danger",
  INFEASIBLE: "text-danger",
};

const same = (a: Itinerary | null, b: Itinerary) =>
  !!a && Math.abs(a.total_seconds - b.total_seconds) < 0.5 && a.total_cost === b.total_cost;

export function choicesFrom(data: TripResponse): Choice[] {
  const out: Choice[] = [];
  const multi = data.segments.length > 1;
  if (data.hard_constraint) {
    out.push({ key: "limit", itinerary: data.hard_constraint, color: "var(--limit)", title: "Every leg under the limit", kind: "limit" });
  }
  let naiveShown = false;
  data.options.forEach((o, i) => {
    // The constrained route usually reappears as the cheapest frontier point and the
    // direct ride as the fastest; each itinerary appears once.
    if (same(data.hard_constraint, o.itinerary)) return;
    const isNaive = same(data.naive, o.itinerary);
    naiveShown ||= isNaive;
    out.push({
      key: `p${i}`,
      itinerary: o.itinerary,
      color: isNaive ? "var(--naive)" : "var(--frontier)",
      title: isNaive ? (multi ? "Direct rides" : "Direct ride") : `Trade-off ${i + 1}`,
      kind: isNaive ? "naive" : "frontier",
    });
  });
  if (data.naive && !naiveShown && !same(data.hard_constraint, data.naive)) {
    out.push({ key: "naive", itinerary: data.naive, color: "var(--naive)", title: multi ? "Direct rides" : "Direct ride", kind: "naive" });
  }
  return out;
}

function Headline({ data }: { data: TripResponse }) {
  const naive = data.naive;
  const hard = data.hard_constraint;
  const visits = data.segments.length;
  const where = visits > 1 ? ` across ${visits} legs${data.round_trip ? ", back to the start" : ""}` : "";
  if (!hard) {
    return (
      <p className="serif text-[26px] leading-[1.15] text-ink">
        No way to keep every ride under the limit{where} — every option below costs something.
      </p>
    );
  }
  if (!naive || same(naive, hard) || naive.total_cost === 0) {
    return (
      <p className="serif text-[26px] leading-[1.15] text-ink">
        {visits > 1 ? "Every ride fits the limit" : "The direct ride fits the limit"}
        {where}. <span className="italic text-muted">Nothing to pay, no reset.</span>
      </p>
    );
  }
  const saving = naive.total_cost - hard.total_cost;
  const delta = (hard.total_seconds - naive.total_seconds) / 60;
  const deltaText = Math.abs(delta) < 0.05 ? "the same time" : delta > 0 ? `${delta.toFixed(1)} more minutes` : `${(-delta).toFixed(1)} fewer minutes`;
  const resets = hard.num_transfers;
  return (
    <p className="serif text-[26px] leading-[1.15] text-ink">
      Reset the clock {resets === 1 ? "once" : `${resets} times`}
      {where} and save <span className="num text-[24px] text-limit">{money(saving)}</span>{" "}
      <span className="italic text-muted">for {deltaText}.</span>
    </p>
  );
}

function Row({ choice, selected, index, onSelect }: { choice: Choice; selected: boolean; index: number; onSelect: () => void }) {
  const it = choice.itinerary;
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      style={{ animationDelay: `${index * 60}ms` }}
      className={`rise grid w-full grid-cols-[14px_1fr_auto] items-baseline gap-x-3 border-t border-rule px-1 py-2.5 text-left transition-colors ${
        selected ? "bg-paper-2" : "hover:bg-paper-2/60"
      }`}
    >
      <span aria-hidden className="mt-1 h-2.5 w-2.5 self-center" style={{ background: choice.color }} />
      <span className="min-w-0">
        <span className="block text-[14px] font-medium text-ink">{choice.title}</span>
        <span className="block text-[12px] text-muted">
          {km(it.total_distance_m)} · {it.num_transfers} reset{it.num_transfers === 1 ? "" : "s"} ·{" "}
          <span className={RISK_CLASS[it.overall_risk]}>{RISK_TEXT[it.overall_risk]}</span>
        </span>
      </span>
      <span className="num text-right">
        <span className="block text-[15px] text-ink">{minutes(it.total_seconds)}</span>
        <span className={`block text-[13px] ${it.total_cost > 0 ? "text-danger" : "text-limit"}`}>{money(it.total_cost)}</span>
      </span>
    </button>
  );
}

/** The selected itinerary as a timetable: cumulative clock on the left, a rail with stops. */
function Timeline({ it, color, title }: { it: Itinerary; color: string; title: string }) {
  const rides = it.legs.filter((l) => l.mode === "RIDE");
  const colors = legColorsFor(it, color);
  const segs = legSegments(it.legs);
  const multi = Math.max(...segs) > 0;
  // Cumulative start time of each leg, and the arrival time.
  const starts = it.legs.reduce<number[]>((acc, leg, i) => [...acc, (acc[i - 1] ?? 0) + (i === 0 ? 0 : it.legs[i - 1].duration_seconds)], []);
  const arrival = it.total_seconds;
  return (
    <ol className="rise relative mt-1 mb-2 pl-1">
      {it.legs.map((leg, i) => {
        const start = starts[i];
        const rail = colors[i];
        const isReset = leg.mode === "RIDE" && it.legs[i + 1]?.mode === "RIDE";
        // Two walks in a row is a visit: dock near the place, come back for a bike later.
        const isVisit = leg.mode === "WALK" && it.legs[i + 1]?.mode === "WALK";
        const last = i === it.legs.length - 1;
        return (
          <li key={i} className="grid grid-cols-[44px_18px_1fr] gap-x-2" style={{ ["--rail" as string]: rail }}>
            <span className="num pt-[3px] text-[12px] text-muted">{clock(start)}</span>
            <span className="relative flex justify-center">
              <span
                aria-hidden
                className="absolute top-2 bottom-[-2px] w-0.5"
                style={{
                  background: leg.mode === "WALK" ? `repeating-linear-gradient(to bottom, var(--walk) 0 3px, transparent 3px 7px)` : "var(--rail)",
                }}
              />
              <span aria-hidden className="relative mt-[5px] h-2.5 w-2.5 rounded-full border-2 bg-paper" style={{ borderColor: i === 0 ? "var(--origin)" : "var(--rail)" }} />
            </span>
            <div className="pb-4">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[14px] text-ink">
                  {multi && (i === 0 || segs[i] !== segs[i - 1]) && (
                    <span className="label mr-1.5 align-[1px]" style={{ color: rail }}>
                      Leg {segs[i] + 1}
                    </span>
                  )}
                  {leg.mode === "WALK" ? "Walk" : "Ride"} <span className="text-muted">to</span> {leg.to_name}
                </span>
                <span className="num shrink-0 text-[12px] text-muted">{minutes(leg.duration_seconds)}</span>
              </div>
              <div className="text-[12px] text-muted">
                {km(leg.distance_m)}
                {leg.mode === "RIDE" && (
                  <>
                    {leg.elevation_gain_m > 0 && <> · +{Math.round(leg.elevation_gain_m)} m climb</>} ·{" "}
                    <span className={RISK_CLASS[leg.risk]}>{RISK_TEXT[leg.risk]}</span>
                    {leg.overage_cost > 0 && <span className="num text-danger"> · {money(leg.overage_cost)}</span>}
                  </>
                )}
              </div>
              {isVisit && (
                <div className="mt-1.5 inline-flex items-center gap-1.5 bg-ink px-1.5 py-0.5 text-[11px] text-paper">
                  <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-paper" />
                  Visit {leg.to_name} — bike docked
                </div>
              )}
              {isReset && (
                <div className="mt-1.5 inline-flex items-center gap-1.5 border border-rule-strong px-1.5 py-0.5 text-[11px] text-ink">
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden>
                    <path d="M21 12a9 9 0 1 1-3-6.7" />
                    <path d="M21 3v6h-6" />
                  </svg>
                  Reset: dock, wait, re-unlock the same bike
                </div>
              )}
            </div>
            {last && (
              <>
                <span className="num text-[12px] text-muted">{clock(arrival)}</span>
                <span className="flex justify-center">
                  <span aria-hidden className="mt-[3px] h-2.5 w-2.5 rounded-sm" style={{ background: "var(--destination)" }} />
                </span>
                <span className="text-[14px] text-ink">Arrive</span>
              </>
            )}
          </li>
        );
      })}
      {rides.length > 0 && (
        <li className="num mt-2 pl-[62px] text-[11px] text-muted">
          riding {minutes(rides.reduce((a, l) => a + l.cycling_seconds, 0))} · lights {minutes(rides.reduce((a, l) => a + l.traffic_light_seconds, 0))} ·
          docking {minutes(rides.reduce((a, l) => a + l.docking_seconds, 0))}
        </li>
      )}
      <li>
        <ShareRow it={it} title={title} />
      </li>
    </ol>
  );
}

export default function ResultsPanel({
  data,
  choices,
  selectedKey,
  onSelect,
  limitMinutes,
}: {
  data: TripResponse;
  choices: Choice[];
  selectedKey: string | null;
  onSelect: (key: string) => void;
  limitMinutes: number;
}) {
  const selected = choices.find((c) => c.key === selectedKey) ?? null;
  return (
    <section className="rise">
      <Headline data={data} />
      {choices.length > 1 && <FrontierChart choices={choices} selectedKey={selectedKey} onSelect={onSelect} limitMinutes={limitMinutes} />}
      <div className="mt-4 border-b border-rule">
        {choices.map((c, i) => (
          <div key={c.key}>
            <Row choice={c} selected={c.key === selectedKey} index={i} onSelect={() => onSelect(c.key)} />
            {selected?.key === c.key && <Timeline it={c.itinerary} color={c.color} title={c.title} />}
          </div>
        ))}
      </div>
      <p className="num mt-3 text-[11px] leading-relaxed text-muted">
        {data.stats.labels_generated.toLocaleString()} labels · {(data.stats.pruned_fraction * 100).toFixed(1)}% pruned by dominance · frontier{" "}
        {data.stats.frontier_size} · {data.stats.planning_ms} ms · walks {data.stats.walk_legs_street_routed ? "street-routed" : "estimated"}
      </p>
    </section>
  );
}
