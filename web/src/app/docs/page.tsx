import Link from "next/link";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "How it works — àVélo Route Optimizer",
  description: "What the planner does, how the time model and the two search algorithms work, and where the data comes from.",
};

function H2({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2 id={id} className="serif mt-14 scroll-mt-6 text-[30px] leading-[1.1] text-ink">
      <a href={`#${id}`} className="no-underline hover:underline">
        {children}
      </a>
    </h2>
  );
}

function P({ children }: { children: React.ReactNode }) {
  return <p className="mt-4 max-w-[62ch] text-[16px] leading-[1.6] text-ink-2">{children}</p>;
}

function Code({ children }: { children: React.ReactNode }) {
  return <code className="num bg-paper-2 px-1 py-0.5 text-[13px] text-ink">{children}</code>;
}

const SECTIONS = [
  ["problem", "The problem"],
  ["using", "Using the planner"],
  ["reset", "Why a reset is free"],
  ["model", "How long a ride takes"],
  ["search", "Two ways to search"],
  ["multi", "Several stops, round trips"],
  ["risk", "Risk, not just feasibility"],
  ["data", "Where the data comes from"],
  ["results", "Does it work?"],
  ["api", "API"],
] as const;

export default function DocsPage() {
  return (
    <div className="min-h-full bg-paper">
      <header className="border-b border-rule">
        <div className="mx-auto flex max-w-[1040px] items-baseline justify-between px-5 py-4">
          <Link href="/" className="flex items-baseline gap-2 no-underline">
            <span className="text-[15px] font-semibold tracking-tight text-ink">àVélo</span>
            <span className="serif text-[19px] italic text-ink-2">Route Optimizer</span>
          </Link>
          <Link href="/" className="label text-ink hover:text-limit">
            ← Plan a trip
          </Link>
        </div>
      </header>

      <div className="mx-auto grid max-w-[1040px] gap-10 px-5 pb-24 pt-10 md:grid-cols-[200px_1fr]">
        <nav className="hidden md:block">
          <ol className="sticky top-6 space-y-2 border-l border-rule pl-4">
            {SECTIONS.map(([id, label], i) => (
              <li key={id}>
                <a href={`#${id}`} className="flex gap-2 text-[13px] text-muted no-underline hover:text-ink">
                  <span className="num w-5 shrink-0">{String(i + 1).padStart(2, "0")}</span>
                  {label}
                </a>
              </li>
            ))}
          </ol>
        </nav>

        <main>
          <p className="label">How it works</p>
          <h1 className="serif mt-2 text-[44px] leading-[1.05] text-ink">
            One bike, one trip, <span className="italic">no overage.</span>
          </h1>
          <P>
            àVélo is Québec City’s bike-share. A plan gives you 30 or 45 minutes per ride; every minute past that costs $0.30. Most trips
            across the city do not fit in one ride, and ordinary navigation apps do not know that. This planner does.
          </P>

          <H2 id="problem">The problem</H2>
          <P>
            The limit applies <em>per ride</em>, not per trip. The clock starts when you unlock and stops when you dock. So the question is
            not “what is the fastest way from A to B” but “where should I dock along the way so that no single ride runs over” — and, if
            you would rather pay than stop, exactly what that costs.
          </P>
          <P>
            Measured over 1,000 real trips in this network, riding straight through overruns a 30-minute plan about half the time, at a
            mean overage of $2.75. See <a href="#results">Does it work?</a>
          </P>

          <H2 id="using">Using the planner</H2>
          <ol className="mt-4 max-w-[62ch] list-decimal space-y-3 pl-5 text-[16px] leading-[1.6] text-ink-2">
            <li>
              <b className="text-ink">Set where you start and where you are going.</b> Search a place, tap the map, tap a station, or use
              your location. Pins can be dragged.
            </li>
            <li>
              <b className="text-ink">Add stops</b> if you are visiting several places, and tick <em>Return to start</em> for a round
              trip. Each visit means docking the bike; the plan picks the stations.
            </li>
            <li>
              <b className="text-ink">Choose your bike and your plan</b> — electric or mechanical, 30 or 45 minutes per ride. Availability
              is live: the counts next to each option are bikes on the street right now.
            </li>
            <li>
              <b className="text-ink">Read the options.</b> The green route keeps every ride under the limit ($0). The others trade money
              for time; the chart shows them all at once. Tap one to see it on the map and as a timetable, then open it in Google Maps or
              Waze, or share the link.
            </li>
          </ol>
          <P>
            Station colours: <span className="text-limit">green</span> has bikes and docks, <span className="text-danger">red</span> has no
            bikes to take, <span className="text-warn">amber</span> has no free dock to reset in. With a route selected, only the stations
            it uses stay on the map.
          </P>

          <H2 id="reset">Why a reset is free</H2>
          <P>
            You keep the same bike for the whole trip. A “reset” is docking at a station, waiting for the ride to close, and unlocking the
            bike again: the clock restarts and nothing is billed. It costs about a minute, so it is only worth doing when the alternative
            is paying. A transfer station therefore needs a free <em>dock</em> and to be in service — it does not need a spare bike, which
            is why empty stations still work as reset points.
          </P>

          <H2 id="model">How long a ride takes</H2>
          <P>
            Everything rests on one estimate: the minutes from station A to station B. It is the sum of riding time, expected traffic-light
            delay, the reset overhead, and a 90-second safety buffer.
          </P>
          <P>
            Riding time uses <b className="text-ink">real bicycle-network distances</b> (not straight lines — the street distance between
            every pair of stations is a median 1.27× the crow-flies distance, measured over 49,626 pairs) and <b className="text-ink">the
            grade</b>. Québec City has 141 m of relief between its lowest and highest stations; the Basse-Ville / Haute-Ville escarpment is
            a wall in the middle of it. Uphill speed decays exponentially with grade, calibrated so a 5% climb halves a mechanical bike
            and takes ~22% off an e-bike; downhill helps less than uphill hurts, because you brake. A 90 m climb over 1.4 km costs an
            electric bike 39% more time than the flat and a mechanical one 145% more.
          </P>
          <P>
            Traffic lights are an expectation, not a worst case: arriving at a random moment in a 60-second cycle you wait 7.5 s on average,
            plus stopping and starting a heavy bike — about 12 s per light, one light every 250 m.
          </P>

          <H2 id="search">Two ways to search</H2>
          <P>
            <b className="text-ink">Every leg under the limit.</b> The station graph has ~25,000 edges, one for every ride a person might
            plausibly take. Remove every edge that runs over the limit and the fastest remaining path is plain Dijkstra — the constraint
            is local to each edge, so nothing cleverer is needed. Recognising that is the point.
          </P>
          <P>
            <b className="text-ink">The trade-offs.</b> Once you may pay to go over, there are two things to minimise at once, time and
            money, and there is no single best answer: a 32-minute direct ride costs 60 cents but saves a reset; a two-ride route is free
            but slower. This is the bi-criteria shortest-path problem. It is solved with Martins’ label-setting algorithm: every station
            keeps a <em>set</em> of partial routes, and a new one is thrown away if an existing one is at least as fast <em>and</em> at
            least as cheap. That pruning — Pareto dominance — discards about 98% of everything generated, which is the only reason the
            search finishes in a couple of hundred milliseconds. What survives at the destination is the frontier: the options you see.
          </P>

          <H2 id="multi">Several stops, round trips</H2>
          <P>
            A visit means docking near the place, so a trip through several stops is a chain of independent door-to-door plans. The
            whole-trip options are the Pareto-optimal <em>combinations</em> of each segment’s options, pruned segment by segment so the
            work never explodes. A round trip is the same chain with the start appended as the last stop. Each leg has its own colour on
            the map and in the timetable.
          </P>

          <H2 id="risk">Risk, not just feasibility</H2>
          <P>
            The estimate is a mean; the real ride is a distribution around it. A ride estimated at 96% of the limit is under it on paper
            and a coin flip in practice. The green route therefore refuses any ride above 95% of the limit and resets at a nearby
            station instead, relaxing that rule only if nothing safer reaches the destination. Every ride is labelled low, medium or high
            risk so you can see where the margin is thin.
          </P>

          <H2 id="data">Where the data comes from</H2>
          <ul className="mt-4 max-w-[62ch] list-disc space-y-2 pl-5 text-[16px] leading-[1.6] text-ink-2">
            <li>
              <b className="text-ink">Stations and availability</b>: àVélo’s public GBFS feed, refreshed every 30 seconds. The same
              code runs on any GBFS system — Bixi, Citi Bike, Vélib’ — by changing one URL.
            </li>
            <li>
              <b className="text-ink">Street distances and polylines</b>: OSRM with OpenStreetMap’s bicycle and foot profiles, self-hosted
              on a Québec City extract; every station pair is pre-computed.
            </li>
            <li>
              <b className="text-ink">Elevation</b>: open-meteo, one lookup per station, cached forever.
            </li>
            <li>
              <b className="text-ink">Place search</b>: Google Places, through the API; OpenStreetMap when unavailable.
            </li>
            <li>
              <b className="text-ink">Pricing and limits</b>: verified against àVélo’s published plans — 30 or 45 minutes, $0.30 per
              extra minute, billed per started minute.
            </li>
          </ul>

          <H2 id="results">Does it work?</H2>
          <P>
            The planner is compared against what a normal app does — nearest station to nearest station, one ride — over 1,000 trips
            sampled where people actually ride, all costed with the same model so the comparison is about the route chosen, not the
            estimate. The naive strategy overruns the limit on roughly half of trips; the constrained route on none, for a mean cost of
            about two minutes. An ablation with the hill model switched off is reported too: for today’s all-electric fleet it
            changes individual legs but rarely the chosen route, which the project says out loud rather than hides. The full tables are in{" "}
            <a href="https://github.com/imranakki/avelo-route-optimizer/blob/main/docs/evaluation.md" target="_blank" rel="noopener noreferrer">
              docs/evaluation.md
            </a>
            .
          </P>

          <H2 id="api">API</H2>
          <P>
            Everything the app does is a public JSON endpoint: <Code>/route</Code>, <Code>/route/options</Code>,{" "}
            <Code>/route/compare</Code>, <Code>/trip</Code>, <Code>/stations</Code>, <Code>/geocode</Code>. Parameters include the bike
            type, the plan (<Code>limit=30|45</Code>), the accepted risk (<Code>max_risk</Code>) and <Code>geometry=true</Code> for
            street polylines. Interactive documentation lives at <Code>/api/docs</Code>; the source, the cost-model derivation and the
            evaluation are on{" "}
            <a href="https://github.com/imranakki/avelo-route-optimizer" target="_blank" rel="noopener noreferrer">
              GitHub
            </a>
            .
          </P>

          <p className="mt-16 border-t border-rule pt-4 text-[13px] text-muted">
            Built by Imran Akki, Université Laval. Not affiliated with àVélo or the RTC.
          </p>
        </main>
      </div>
    </div>
  );
}
