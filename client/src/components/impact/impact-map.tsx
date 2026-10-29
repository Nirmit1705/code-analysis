import { useId, useMemo, type CSSProperties } from "react";

import { hopsText, mapLabel, ringLabel, type Reach, type ReachItem } from "@/lib/impact-view";
import { cn } from "@/lib/utils";

/** Radar-style map of what a commit reaches: the commit in the middle, one
 * ring per hop, endpoints on the outermost dashed ring. Rings are depth only —
 * the API doesn't say which file imports which, so the only lines drawn are
 * from the commit to the files that import it directly. Styles live in
 * `styles.css` (`.im-*`); the sweep that reveals the points plays once. */

const CX = 280;
const CY = 280;
const R_FIRST = 62;
const R_LAST = 238;
const BEZEL = 262;
const SPAN_DEG = 316; // leaves a gap at the top for the ring labels
const SWEEP_SECONDS = 1.6;
const RAD = Math.PI / 180;

const TICKS = Array.from({ length: 72 }, (_, t) => {
  const a = t * 5 * RAD;
  const len = t % 6 === 0 ? 9 : 4;
  return {
    x1: CX + BEZEL * Math.cos(a),
    y1: CY + BEZEL * Math.sin(a),
    x2: CX + (BEZEL - len) * Math.cos(a),
    y2: CY + (BEZEL - len) * Math.sin(a),
  };
});

interface Placed {
  item: ReachItem;
  x: number;
  y: number;
  /** Seconds into the sweep at which the sweep line passes this point. */
  delay: number;
}

function place(reach: Reach) {
  const rings = Math.max(reach.ringCount, 1);
  const step = (R_LAST - R_FIRST) / rings;
  const radius = (ring: number) => R_FIRST + ring * step;

  const byRing = new Map<number, ReachItem[]>();
  for (const item of reach.items) {
    const list = byRing.get(item.ring);
    if (list) list.push(item);
    else byRing.set(item.ring, [item]);
  }

  const placed: Placed[] = [];
  byRing.forEach((items, ring) => {
    const start = -68 + (ring % 2) * 5;
    items.forEach((item, k) => {
      const deg = start + (SPAN_DEG * (k + 0.5)) / items.length;
      const r = radius(ring);
      placed.push({
        item,
        x: CX + r * Math.cos(deg * RAD),
        y: CY + r * Math.sin(deg * RAD),
        delay: ((deg + 90) / 360) * SWEEP_SECONDS,
      });
    });
  });
  return { placed, rings, radius, byRing };
}

function Tip({ p }: { p: Placed }) {
  const text = `${mapLabel(p.item)} · ${hopsText(p.item)}`;
  const w = text.length * 7.3 + 18;
  let x0 = p.x + 14;
  if (x0 + w > 552) x0 = p.x - 14 - w;
  if (x0 < 8) x0 = 8;
  let y0 = p.y - 36;
  if (y0 < 8) y0 = p.y + 16;
  return (
    <g pointerEvents="none">
      <rect className="im-tip-bg" x={x0} y={y0} width={w} height={26} rx={7} />
      <text className="im-tip-text" x={x0 + 9} y={y0 + 17}>
        {text}
      </text>
    </g>
  );
}

export function ImpactMap({
  reach,
  commitSha,
  activeId,
  onActive,
}: {
  reach: Reach;
  commitSha: string;
  activeId: string | null;
  onActive: (id: string | null) => void;
}) {
  const uid = useId().replace(/:/g, "");
  const { placed, rings, radius, byRing } = useMemo(() => place(reach), [reach]);
  const activePoint = activeId ? placed.find((p) => p.item.id === activeId) : undefined;

  const ringIndexes = Array.from({ length: rings }, (_, i) => i + 1);
  const sweepFrom = -150 * RAD;

  return (
    <svg
      viewBox="0 0 560 560"
      role="img"
      aria-label="Map of the files and endpoints this commit reaches, arranged in rings by number of hops. The same items are listed beside it."
      className="impact-map mx-auto block h-auto max-h-[560px] w-full"
    >
      <defs>
        <radialGradient id={`${uid}-wash`} cx="50%" cy="50%" r="50%">
          <stop offset="0" style={{ stopColor: "var(--violet)", stopOpacity: 0.22 }} />
          <stop offset="0.5" style={{ stopColor: "var(--violet)", stopOpacity: 0.06 }} />
          <stop offset="1" style={{ stopColor: "var(--violet)", stopOpacity: 0 }} />
        </radialGradient>
        <linearGradient
          id={`${uid}-sweep`}
          gradientUnits="userSpaceOnUse"
          x1={CX - R_LAST * 0.87}
          y1={CY}
          x2={CX}
          y2={CY}
        >
          <stop offset="0" style={{ stopColor: "var(--violet)", stopOpacity: 0 }} />
          <stop offset="1" style={{ stopColor: "var(--violet)", stopOpacity: 0.34 }} />
        </linearGradient>
      </defs>

      <circle cx={CX} cy={CY} r={BEZEL} fill={`url(#${uid}-wash)`} />
      <circle className="im-bezel" cx={CX} cy={CY} r={BEZEL} />
      {TICKS.map((t, i) => (
        <line key={i} className="im-tick" x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2} />
      ))}

      {ringIndexes.map((i) => (
        <g key={i}>
          <circle
            className={cn("im-ring", i === reach.apiRing && "im-ring-ep")}
            cx={CX}
            cy={CY}
            r={radius(i)}
          />
          <text className="im-ring-label" x={CX} y={CY - radius(i) + 4}>
            {ringLabel(reach, i, byRing.get(i)?.length ?? 0)}
          </text>
        </g>
      ))}

      {placed
        .filter((p) => p.item.ring === 1 && p.item.kind !== "api")
        .map((p) => (
          <line
            key={`spoke-${p.item.id}`}
            className="im-spoke"
            style={{ "--dl": `${p.delay.toFixed(2)}s` } as CSSProperties}
            x1={CX}
            y1={CY}
            x2={p.x}
            y2={p.y}
          />
        ))}

      <g className="im-sweep">
        <path
          d={`M ${CX} ${CY} L ${(CX + R_LAST * Math.cos(sweepFrom)).toFixed(1)} ${(CY + R_LAST * Math.sin(sweepFrom)).toFixed(1)} A ${R_LAST} ${R_LAST} 0 0 1 ${CX} ${CY - R_LAST} Z`}
          fill={`url(#${uid}-sweep)`}
        />
        <line
          x1={CX}
          y1={CY}
          x2={CX}
          y2={CY - R_LAST}
          style={{ stroke: "var(--violet)", strokeOpacity: 0.75, strokeWidth: 1.2 }}
        />
      </g>

      {placed.map((p) => {
        const { item } = p;
        const r = item.ring === 1 ? 6.5 : 5.5;
        return (
          <g
            key={item.id}
            aria-hidden="true"
            className={cn(
              "im-pt",
              `im-${item.kind}`,
              `im-r${Math.min(item.ring, 4)}`,
              activeId === item.id && "is-hot",
            )}
            style={{ "--dl": `${p.delay.toFixed(2)}s` } as CSSProperties}
            onMouseEnter={() => onActive(item.id)}
            onMouseLeave={() => onActive(null)}
          >
            <circle className="im-halo" cx={p.x} cy={p.y} r={14} />
            {item.kind === "api" ? (
              <path
                className="im-dot"
                d={`M${p.x.toFixed(1)} ${(p.y - 6.5).toFixed(1)}L${(p.x + 6.5).toFixed(1)} ${p.y.toFixed(1)}L${p.x.toFixed(1)} ${(p.y + 6.5).toFixed(1)}L${(p.x - 6.5).toFixed(1)} ${p.y.toFixed(1)}Z`}
              />
            ) : (
              <circle className="im-dot" cx={p.x} cy={p.y} r={r} />
            )}
          </g>
        );
      })}

      <circle className="im-core-glow" cx={CX} cy={CY} r={46} />
      <circle className="im-core" cx={CX} cy={CY} r={31} />
      <text className="im-core-text" x={CX} y={CY + 4}>
        {commitSha.slice(0, 7)}
      </text>

      {activePoint ? <Tip p={activePoint} /> : null}
    </svg>
  );
}
