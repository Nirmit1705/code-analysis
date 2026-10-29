/** Pure view helpers for the Impact Analysis screen (`routes/app.impact.tsx`,
 * `components/impact/*`): risk styling, the verdict wording and the flat list
 * of files/endpoints a commit reaches that both the map and the list render.
 * Everything here is derived from `ImpactRead` — no signal the backend doesn't
 * return is invented. */
import type { ImpactRead, ImpactRisk } from "@/lib/queries/impact";

/** Mirrors `_RISK_SCORE_THRESHOLDS` (medium, high) in
 * `server/app/services/impact_service.py`. Small constants are duplicated per
 * module on purpose in this codebase; keep the two in step. */
export const MEDIUM_FROM = 20;
export const HIGH_FROM = 50;

export interface RiskStyle {
  word: string;
  text: string;
  bar: string;
}

const RISK_STYLE: Record<ImpactRisk, RiskStyle> = {
  LOW: { word: "Low risk", text: "text-success", bar: "bg-success" },
  MEDIUM: { word: "Medium risk", text: "text-warning", bar: "bg-warning" },
  HIGH: { word: "High risk", text: "text-danger", bar: "bg-danger" },
};

export function riskStyle(risk: ImpactRisk): RiskStyle {
  return RISK_STYLE[risk];
}

export function formatPoints(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export function shortSha(sha: string): string {
  return sha.slice(0, 7);
}

/** Same rule as the server's `_is_test` (impact_service.py): a test-looking
 * directory anywhere above the file, or a test-looking file name. The server
 * returns only a count of excluded tests, not which files they are. */
const TEST_DIR_NAMES = new Set(["__tests__", "tests", "test", "spec", "specs", "e2e"]);
const TEST_FILE_RE = /(\.(test|spec)\.[^./]+$)|(^test_[^/]*\.py$)|(_test\.py$)|(Tests?\.java$)/;

export function isTestPath(path: string): boolean {
  const parts = path.split("/");
  const name = parts[parts.length - 1] ?? "";
  return (
    parts.slice(0, -1).some((p) => TEST_DIR_NAMES.has(p.toLowerCase())) || TEST_FILE_RE.test(name)
  );
}

export function splitPath(path: string): { dir: string; name: string } {
  const i = path.lastIndexOf("/");
  return { dir: path.slice(0, i + 1), name: path.slice(i + 1) };
}

export function verdictSentence(data: ImpactRead): string {
  const files = data.direct.length + data.indirect.length;
  const apis = data.affected_apis.length;
  if (files + apis === 0) return "Nothing downstream depends on this commit.";
  const parts: string[] = [];
  if (files > 0) parts.push(plural(files, "file"));
  if (apis > 0) parts.push(plural(apis, "endpoint"));
  return `${parts.join(" and ")} sit${parts.length === 1 && files + apis === 1 ? "s" : ""} downstream of this commit.`;
}

/** Built from `ImpactRead.breakdown` (the server's own arithmetic), so it only
 * states what the response backs up. */
export function verdictNote(data: ImpactRead): string {
  const files = data.direct.length + data.indirect.length;
  if (files + data.affected_apis.length === 0) {
    return "The files it changed are not imported anywhere else in the repository.";
  }
  const b = data.breakdown;
  let note =
    b.change_kind === "breaking"
      ? "A file, symbol or endpoint was removed, so the score counts in full."
      : `Nothing was removed, so the score is scaled to ${Math.round(b.multiplier * 100)}%.`;
  if (b.cycle_bonus > 0) {
    note += ` A changed file is part of a dependency cycle (+${formatPoints(b.cycle_bonus)}).`;
  }
  return note;
}

export type ReachKind = "file" | "test" | "api";

export interface ReachItem {
  /** Unique within one run; the map and the list share it for hover sync. */
  id: string;
  kind: ReachKind;
  /** Ring on the map: hop count for files, one past the last hop for endpoints. */
  ring: number;
  path: string;
  /** HTTP method, endpoints only. */
  method: string | null;
  /** The server's "imports `x`" sentence, files only. */
  why: string | null;
  hops: number | null;
}

export interface Reach {
  items: ReachItem[];
  /** Number of rings to draw (file rings + the endpoint ring when there is one). */
  ringCount: number;
  /** Ring holding the endpoints, or null when no endpoint is reached. */
  apiRing: number | null;
  /** Deepest file ring. */
  maxHop: number;
}

export function splitEndpoint(endpoint: string): { method: string | null; path: string } {
  const i = endpoint.indexOf(" ");
  return i > 0
    ? { method: endpoint.slice(0, i), path: endpoint.slice(i + 1) }
    : { method: null, path: endpoint };
}

export function buildReach(data: ImpactRead): Reach {
  const items: ReachItem[] = [];
  const seen = new Set<string>();
  let maxHop = 1;

  const addFile = (path: string, why: string | null, hops: number) => {
    const id = `file:${path}`;
    if (seen.has(id)) return;
    seen.add(id);
    if (hops > maxHop) maxHop = hops;
    items.push({
      id,
      kind: isTestPath(path) ? "test" : "file",
      ring: hops,
      path,
      method: null,
      why,
      hops,
    });
  };
  for (const f of data.direct) addFile(f.path, f.why, f.hops ?? 1);
  for (const f of data.indirect) addFile(f.path, f.why, f.hops ?? 2);

  const hasApis = data.affected_apis.length > 0;
  const apiRing = hasApis ? maxHop + 1 : null;
  if (apiRing !== null) {
    for (const a of data.affected_apis) {
      const id = `api:${a}`;
      if (seen.has(id)) continue;
      seen.add(id);
      const { method, path } = splitEndpoint(a);
      items.push({ id, kind: "api", ring: apiRing, path, method, why: null, hops: null });
    }
  }
  return { items, ringCount: maxHop + (hasApis ? 1 : 0), apiRing, maxHop };
}

export function hopsText(item: ReachItem): string {
  if (item.kind === "api") return "endpoint";
  const h = item.hops ?? item.ring;
  return `${h} ${h === 1 ? "hop" : "hops"}`;
}

/** Short name drawn in the map's hover label; the full path goes below the map. */
export function mapLabel(item: ReachItem): string {
  const text =
    item.kind === "api" ? `${item.method ?? ""} ${item.path}`.trim() : splitPath(item.path).name;
  return text.length > 40 ? `${text.slice(0, 39)}…` : text;
}

export function ringLabel(reach: Reach, ring: number, count: number): string {
  const name = ring === reach.apiRing ? "endpoints" : ring === 1 ? "1 hop" : `${ring} hops`;
  return `${name} · ${count}`;
}
