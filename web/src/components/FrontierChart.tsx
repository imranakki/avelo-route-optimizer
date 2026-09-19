"use client";

import type { Choice } from "./ResultsPanel";

// Time on the x axis, money on the y axis: every candidate itinerary is a point,
// and the Pareto frontier is the lower-left edge. Clicking a point selects it.
export default function FrontierChart({
  choices,
  selectedKey,
  onSelect,
  limitMinutes,
}: {
  choices: Choice[];
  selectedKey: string | null;
  onSelect: (key: string) => void;
  limitMinutes: number;
}) {
  const W = 360;
  const H = 132;
  const L = 34;
  const R = 12;
  const T = 12;
  const B = 26;
  const times = choices.map((c) => c.itinerary.total_seconds / 60);
  const costs = choices.map((c) => c.itinerary.total_cost);
  const tMin = Math.floor(Math.min(...times) - 1);
  const tMax = Math.ceil(Math.max(...times) + 1);
  const cMax = Math.max(0.3, Math.ceil(Math.max(...costs) / 0.3) * 0.3);
  const x = (t: number) => L + ((t - tMin) / (tMax - tMin)) * (W - L - R);
  const y = (c: number) => T + (1 - c / cMax) * (H - T - B);
  const cTicks = [0, cMax / 2, cMax];
  const tTicks = [tMin, (tMin + tMax) / 2, tMax];
  const ordered = [...choices].sort((a, b) => a.itinerary.total_seconds - b.itinerary.total_seconds);

  return (
    <figure className="mt-3">
      <svg viewBox={`0 0 ${W} ${H}`} className="block w-full" role="img" aria-label="Time versus money for each itinerary">
        {cTicks.map((c) => (
          <g key={`c${c}`}>
            <line x1={L} x2={W - R} y1={y(c)} y2={y(c)} stroke="var(--rule)" strokeWidth="1" />
            <text x={L - 6} y={y(c) + 3} textAnchor="end" className="num" fontSize="9.5" fill="var(--muted)">
              ${c.toFixed(2)}
            </text>
          </g>
        ))}
        {tTicks.map((t, i) => (
          <text
            key={`t${t}`}
            x={x(t)}
            y={H - 8}
            textAnchor={i === 0 ? "start" : i === tTicks.length - 1 ? "end" : "middle"}
            className="num"
            fontSize="9.5"
            fill="var(--muted)"
          >
            {Math.round(t)} min
          </text>
        ))}
        {/* the frontier edge, drawn through the points in time order */}
        <polyline
          points={ordered.map((c) => `${x(c.itinerary.total_seconds / 60)},${y(c.itinerary.total_cost)}`).join(" ")}
          fill="none"
          stroke="var(--rule-strong)"
          strokeWidth="1"
          strokeDasharray="2 3"
        />
        {choices.map((c) => {
          const cx = x(c.itinerary.total_seconds / 60);
          const cy = y(c.itinerary.total_cost);
          const on = c.key === selectedKey;
          return (
            <g key={c.key} onClick={() => onSelect(c.key)} className="cursor-pointer">
              <circle cx={cx} cy={cy} r="11" fill="transparent" />
              {on && <circle cx={cx} cy={cy} r="8" fill="none" stroke={c.color} strokeWidth="1.2" />}
              <circle cx={cx} cy={cy} r={on ? 4.5 : 3.5} fill={c.color} stroke="var(--paper)" strokeWidth="1.5" />
            </g>
          );
        })}
      </svg>
      <figcaption className="label mt-1">
        Each point is an itinerary · limit {limitMinutes} min · {choices.length} option{choices.length === 1 ? "" : "s"}
      </figcaption>
    </figure>
  );
}
