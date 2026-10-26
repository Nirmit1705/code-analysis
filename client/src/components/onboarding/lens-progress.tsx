import { cn } from "@/lib/utils";

// The brand ring as a progress dial: the open ring is the track, an orange arc
// fills along it, and the "pupil" rides the head of the arc. Geometry matches the
// logo (gap starts at 82 degrees); both the arc and the pupil ease between
// values, so each stage change slides rather than jumps.

const RADIUS = 100;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
/** Fraction of the circle the track covers; the rest is the lens gap. */
const TRACK_FRACTION = 0.793;
const TRACK = CIRCUMFERENCE * TRACK_FRACTION;
const START_DEG = 82;

export function LensProgress({
  value,
  label,
  caption,
  tone = "primary",
  className,
}: {
  /** 0 to 100. */
  value: number;
  /** Large text in the middle. */
  label: string;
  caption?: string;
  tone?: "primary" | "danger";
  className?: string;
}) {
  const pct = Math.max(0, Math.min(100, value));
  const arc = (pct / 100) * TRACK;
  const pupilDeg = START_DEG + (pct / 100) * TRACK_FRACTION * 360;

  return (
    <div className={cn("relative size-60", className)}>
      <svg
        viewBox="0 0 240 240"
        role="img"
        aria-label={`${label}${caption ? `, ${caption}` : ""}`}
        className="absolute inset-0 size-full"
      >
        <g transform={`rotate(${START_DEG} 120 120)`}>
          <circle
            cx={120}
            cy={120}
            r={RADIUS}
            fill="none"
            strokeWidth={14}
            strokeLinecap="round"
            strokeDasharray={`${TRACK} ${CIRCUMFERENCE}`}
            className="stroke-border"
          />
          <circle
            cx={120}
            cy={120}
            r={RADIUS}
            fill="none"
            strokeWidth={14}
            strokeLinecap="round"
            style={{ strokeDasharray: `${arc} ${CIRCUMFERENCE * 2}` }}
            className={cn(
              "transition-[stroke-dasharray] duration-700 ease-out motion-reduce:transition-none",
              tone === "danger" ? "stroke-danger" : "stroke-primary",
            )}
          />
        </g>
        <g
          style={{ transform: `rotate(${pupilDeg}deg)`, transformOrigin: "120px 120px" }}
          className="transition-transform duration-700 ease-out motion-reduce:transition-none"
        >
          <circle cx={220} cy={120} r={11} className="fill-foreground" />
          <circle cx={220} cy={120} r={5} className="fill-surface" />
        </g>
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-[52px] font-semibold leading-none tracking-[-0.03em]">{label}</span>
        {caption ? (
          <span className="mt-2 text-[11.5px] text-muted-foreground">{caption}</span>
        ) : null}
      </div>
    </div>
  );
}
