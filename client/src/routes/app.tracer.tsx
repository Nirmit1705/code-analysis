import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ChevronDown, ExternalLink, FileCode2, Search, Route as RouteIcon } from "lucide-react";
import { z } from "zod";

import { Panel, PanelHeader, Tag } from "@/components/ui-kit";
import { Skeleton } from "@/components/ui/skeleton";
import { useActiveRepositoryId } from "@/lib/active-repo";
import { useApiEndpoints, type ApiEndpointRead } from "@/lib/queries/apiReference";
import { useFileTree, type FileTreeEntry } from "@/lib/queries/codebase";
import { useRepositories } from "@/lib/queries/repos";
import {
  LAYER_LABEL,
  LAYER_ORDER,
  useTrace,
  useTraceNotes,
  type TraceHop,
  type TraceLayer,
  type TraceTarget,
  type TracerRead,
} from "@/lib/queries/tracer";
import { cn } from "@/lib/utils";

// `endpoint` ("GET /api/auth/google") and `file` (a repo-relative path) deep-link
// a trace. They name the thing rather than carry a row id because ids belong to
// one analysis run and stop resolving after the next one. A name that no longer
// resolves shows a message instead of silently tracing something else.
const searchSchema = z.object({
  endpoint: z.coerce.string().optional(),
  file: z.coerce.string().optional(),
});

export const Route = createFileRoute("/app/tracer")({
  validateSearch: searchSchema,
  head: () => ({
    meta: [
      { title: "Flow Tracer — CodeLens AI" },
      { name: "description", content: "Follow a request from its route through controllers, services and repositories, using the real import graph." },
      { property: "og:title", content: "Flow Tracer — CodeLens AI" },
      { property: "og:description", content: "The files a request passes through, layer by layer, computed from imports — not guessed." },
    ],
  }),
  component: Tracer,
});

// Layer colours follow the Architecture screen's rule (arch-graph `KIND_META`):
// the theme's own accent tokens are near-identical oranges, so layers get their
// own equal-lightness hues.
const LAYER_COLOR: Record<TraceLayer, string> = {
  route: "oklch(0.77 0.17 52)",
  controller: "oklch(0.76 0.12 250)",
  service: "oklch(0.74 0.15 305)",
  repository: "oklch(0.76 0.15 155)",
  model: "oklch(0.8 0.11 205)",
  other: "oklch(0.7 0.02 255)",
};

const methodTone: Record<string, string> = {
  GET: "text-success border-success/30 bg-success/10",
  POST: "text-info border-info/30 bg-info/10",
  PUT: "text-warning border-warning/30 bg-warning/10",
  PATCH: "text-warning border-warning/30 bg-warning/10",
  DELETE: "text-danger border-danger/30 bg-danger/10",
};

function MethodBadge({ method }: { method: string }) {
  return (
    <span
      className={cn(
        "rounded border px-1.5 py-0.5 font-mono text-[10px]",
        methodTone[method] ?? "text-muted-foreground border-border bg-surface-2",
      )}
    >
      {method}
    </span>
  );
}

// Same path heuristic the tracer excludes by (server/app/analysis/trace.py): a
// test file is never a hop, so it's not offered as a place to start either.
const TEST_PATH = /(^|\/)(__tests__|tests?|specs?|e2e)\/|\.(test|spec)\.[^./]+$|(^|\/)test_[^/]*\.py$|_test\.py$|Tests?\.java$/i;

const endpointKey = (e: Pick<ApiEndpointRead, "method" | "path">) => `${e.method} ${e.path}`;
const baseName = (p: string) => p.slice(p.lastIndexOf("/") + 1);
const dirName = (p: string) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/") + 1) : "");

function Tracer() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();

  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const api = useApiEndpoints(activeRepositoryId);
  const tree = useFileTree(activeRepositoryId);

  const endpoints = useMemo(() => (api.data?.analyzed ? api.data.endpoints : []), [api.data]);
  const files = useMemo(
    () => (tree.data?.analyzed ? tree.data.files.filter((f) => f.language !== null && !TEST_PATH.test(f.path)) : []),
    [tree.data],
  );

  // What to trace: only what the URL names. There is deliberately no default
  // endpoint — the first one is often an entry file (`server.ts`) that mounts
  // every router, and opening on a cut-off 25-file trace would misrepresent
  // what the tool is for. With nothing chosen the screen offers starting points.
  const resolution = useMemo(() => {
    if (search.endpoint) {
      const found = endpoints.find((e) => endpointKey(e) === search.endpoint);
      return found
        ? { target: { endpointId: found.id } as TraceTarget, label: endpointKey(found), missing: null }
        : { target: null, label: search.endpoint, missing: search.endpoint };
    }
    if (search.file) {
      const found = (tree.data?.analyzed ? tree.data.files : []).find((f) => f.path === search.file);
      return found
        ? { target: { fileId: found.id } as TraceTarget, label: found.path, missing: null }
        : { target: null, label: search.file, missing: search.file };
    }
    return { target: null, label: null, missing: null };
  }, [search.endpoint, search.file, endpoints, tree.data]);

  const trace = useTrace(activeRepositoryId, resolution.target);
  const traceReady = trace.data?.analyzed === true;
  const notes = useTraceNotes(activeRepositoryId, resolution.target, traceReady);

  const listsLoading = api.isLoading || tree.isLoading;
  const analyzed = api.data?.analyzed === true;
  const canPick = analyzed && (endpoints.length > 0 || files.length > 0);

  const pickEndpoint = (e: ApiEndpointRead) => void navigate({ search: { endpoint: endpointKey(e) } });
  const pickFile = (f: FileTreeEntry) => void navigate({ search: { file: f.path } });

  const data = trace.data;
  const subtitle =
    data?.analyzed && data.hops.length > 0
      ? `${data.hops.length} ${data.hops.length === 1 ? "file" : "files"} on the path from ${data.start?.label ?? "the start"}, computed from the import graph.`
      : "The files a request passes through, computed from the import graph.";

  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-[17px] font-semibold tracking-tight">Flow Tracer</h1>
          <p className="text-[12px] text-muted-foreground">{subtitle}</p>
        </div>
        {canPick ? (
          <div className="ml-auto min-w-0 max-w-full">
            <TracePicker
              label={resolution.label}
              endpoints={endpoints}
              files={files}
              onPickEndpoint={pickEndpoint}
              onPickFile={pickFile}
            />
          </div>
        ) : null}
      </div>

      {repositories.isLoading ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-subtle">Loading…</p>
        </Panel>
      ) : activeRepositoryId === null ? (
        <Panel>
          <div className="p-10 text-center">
            <p className="text-[13px] text-subtle">No repository connected yet.</p>
            <Link to="/onboarding" className="focus-ring mt-2 inline-block text-[12.5px] text-violet-soft hover:underline">
              Connect a repository
            </Link>
          </div>
        </Panel>
      ) : listsLoading ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-subtle">Loading endpoints…</p>
        </Panel>
      ) : api.isError || tree.isError || !api.data ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-danger">Failed to load this repository's endpoints and files.</p>
        </Panel>
      ) : !analyzed ? (
        <Panel>
          <div className="p-10 text-center">
            <p className="text-[13px] text-subtle">This repository hasn't been analyzed yet.</p>
            <Link to="/onboarding" className="focus-ring mt-2 inline-block text-[12.5px] text-violet-soft hover:underline">
              Run the first analysis
            </Link>
          </div>
        </Panel>
      ) : resolution.missing !== null ? (
        <Panel>
          <div className="p-10 text-center">
            <p className="text-[13px] text-foreground">
              <span className="font-mono">{resolution.missing}</span> isn't in the latest analysis.
            </p>
            <p className="mt-1 text-[12px] text-subtle">
              It may have been renamed or removed since this link was made. Pick something to trace from the list above.
            </p>
          </div>
        </Panel>
      ) : resolution.target === null ? (
        endpoints.length > 0 ? (
          <StartHere endpoints={endpoints} onPick={pickEndpoint} />
        ) : (
          <Panel>
            <p className="p-10 text-center text-[13px] text-subtle">
              No API endpoints were detected. Use the picker above to start from a source file instead.
            </p>
          </Panel>
        )
      ) : trace.isLoading ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-subtle">Tracing…</p>
        </Panel>
      ) : trace.isError || !data ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-danger">Failed to load this trace.</p>
        </Panel>
      ) : !data.analyzed || data.hops.length === 0 ? (
        <Panel>
          <p className="p-10 text-center text-[13px] text-subtle">This repository hasn't been analyzed yet.</p>
        </Panel>
      ) : (
        <TraceView
          key={`${data.analysis_run_id}:${data.start?.label}`}
          data={data}
          notes={notes}
        />
      )}
    </div>
  );
}

// --- starting points -------------------------------------------------------------------

const START_HERE_LIMIT = 12;

/** Shown until something is chosen. A trace is per *file*, so every endpoint in
 * one route file gives the same result; offering one per file gives a list of
 * genuinely different traces rather than twelve copies of the same one. */
function StartHere({ endpoints, onPick }: { endpoints: ApiEndpointRead[]; onPick: (e: ApiEndpointRead) => void }) {
  const byFile = useMemo(() => {
    const groups = new Map<string, ApiEndpointRead[]>();
    for (const e of endpoints) groups.set(e.file_path, [...(groups.get(e.file_path) ?? []), e]);
    return [...groups.values()];
  }, [endpoints]);

  return (
    <Panel ambient>
      <PanelHeader
        title="Choose where to start"
        subtitle="A trace follows the files a request can reach from one endpoint or file"
        icon={<RouteIcon className="size-3.5" />}
      />
      <ul className="divide-y divide-border">
        {byFile.slice(0, START_HERE_LIMIT).map((group) => {
          const e = group[0]!;
          return (
            <li key={e.file_path}>
              <button
                type="button"
                onClick={() => onPick(e)}
                className="focus-ring flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-surface-2"
              >
                <span className="w-14 shrink-0">
                  <MethodBadge method={e.method} />
                </span>
                <span className="min-w-0 flex-1 truncate font-mono text-[12.5px]">{e.path}</span>
                <span className="shrink-0 text-[11.5px] text-subtle">
                  {group.length > 1 ? `+${group.length - 1} more in ` : "in "}
                  <span className="font-mono">{baseName(e.file_path)}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <p className="border-t border-border px-4 py-2.5 text-[11.5px] text-subtle">
        {byFile.length > START_HERE_LIMIT
          ? `One endpoint per route file, ${START_HERE_LIMIT} of ${byFile.length} files. `
          : "One endpoint per route file. "}
        Use the picker above to search every endpoint, or to start from any source file.
      </p>
    </Panel>
  );
}

// --- picker ------------------------------------------------------------------------

const PICKER_LIMIT = 80;

function TracePicker({
  label,
  endpoints,
  files,
  onPickEndpoint,
  onPickFile,
}: {
  label: string | null;
  endpoints: ApiEndpointRead[];
  files: FileTreeEntry[];
  onPickEndpoint: (e: ApiEndpointRead) => void;
  onPickFile: (f: FileTreeEntry) => void;
}) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"endpoints" | "files">(endpoints.length > 0 ? "endpoints" : "files");
  const [q, setQ] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    // Close on an outside press only, by checking the event target (the same
    // approach the Compare run picker documents): a React stopPropagation can't
    // keep a document-level listener from firing, and closing on mousedown
    // would unmount an option before its click landed.
    const close = (e: MouseEvent) => {
      if (e.target instanceof Element && e.target.closest("[data-trace-picker]")) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    inputRef.current?.focus();
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const needle = q.trim().toLowerCase();
  const endpointMatches = useMemo(
    () => endpoints.filter((e) => needle === "" || `${e.method} ${e.path} ${e.file_path}`.toLowerCase().includes(needle)),
    [endpoints, needle],
  );
  const fileMatches = useMemo(
    () => files.filter((f) => needle === "" || f.path.toLowerCase().includes(needle)),
    [files, needle],
  );

  const shownEndpoints = endpointMatches.slice(0, PICKER_LIMIT);
  const shownFiles = fileMatches.slice(0, PICKER_LIMIT);
  const choose = (fn: () => void) => {
    fn();
    setOpen(false);
    setQ("");
  };

  return (
    <div className="relative max-w-full" data-trace-picker>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="focus-ring flex max-w-[min(460px,100%)] min-w-[min(280px,100%)] items-center gap-2 rounded-md border border-border bg-surface-2 px-2.5 py-2 text-left transition-colors hover:border-border-strong hover:bg-surface-3"
      >
        <RouteIcon className="size-3.5 shrink-0 text-subtle" />
        <span className="min-w-0 flex-1 truncate font-mono text-[12.5px]">{label ?? "Choose where to start"}</span>
        <ChevronDown className={cn("size-3.5 shrink-0 text-subtle transition-transform", open && "rotate-180")} />
      </button>

      {open ? (
        <div className="absolute top-[calc(100%+6px)] right-0 z-30 w-[min(520px,calc(100vw-2rem))] overflow-hidden rounded-lg border border-border-strong bg-popover shadow-[var(--shadow-pop)]">
          <div className="flex items-center gap-2 border-b border-border px-3 py-2">
            <Search className="size-3.5 text-subtle" />
            <input
              ref={inputRef}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key !== "Enter") return;
                if (tab === "endpoints" && shownEndpoints[0]) choose(() => onPickEndpoint(shownEndpoints[0]!));
                if (tab === "files" && shownFiles[0]) choose(() => onPickFile(shownFiles[0]!));
              }}
              placeholder={tab === "endpoints" ? "Search by method, path or file" : "Search by file path"}
              aria-label="Search where to start the trace"
              className="min-w-0 flex-1 bg-transparent text-[12.5px] outline-none placeholder:text-subtle"
            />
          </div>
          <div className="flex gap-1 border-b border-border px-2 py-1.5" role="tablist">
            {(
              [
                ["endpoints", "Endpoints", endpointMatches.length],
                ["files", "Files", fileMatches.length],
              ] as const
            ).map(([id, text, count]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={tab === id}
                onClick={() => {
                  setTab(id);
                  inputRef.current?.focus();
                }}
                className={cn(
                  "focus-ring rounded-md px-2.5 py-1 text-[12px] transition-colors",
                  tab === id ? "bg-violet/14 text-foreground" : "text-muted-foreground hover:bg-surface-2",
                )}
              >
                {text} <span className="font-mono text-[10.5px] text-subtle">{count}</span>
              </button>
            ))}
          </div>

          <div className="max-h-[22rem] overflow-y-auto p-1" role="listbox">
            {tab === "endpoints" ? (
              shownEndpoints.length === 0 ? (
                <p className="p-4 text-center text-[12px] text-subtle">No endpoints match.</p>
              ) : (
                shownEndpoints.map((e) => (
                  <button
                    key={e.id}
                    type="button"
                    role="option"
                    aria-selected={label === endpointKey(e)}
                    onClick={() => choose(() => onPickEndpoint(e))}
                    className={cn(
                      "flex w-full items-center gap-2.5 rounded-sm px-2 py-1.5 text-left hover:bg-surface-2",
                      label === endpointKey(e) && "bg-violet/10",
                    )}
                  >
                    <span className="w-14 shrink-0">
                      <MethodBadge method={e.method} />
                    </span>
                    <span className="min-w-0 flex-1 truncate font-mono text-[12px]">{e.path}</span>
                    <span className="max-w-[40%] shrink-0 truncate font-mono text-[10.5px] text-subtle">{baseName(e.file_path)}</span>
                  </button>
                ))
              )
            ) : shownFiles.length === 0 ? (
              <p className="p-4 text-center text-[12px] text-subtle">No source files match.</p>
            ) : (
              shownFiles.map((f) => (
                <button
                  key={f.id}
                  type="button"
                  role="option"
                  aria-selected={label === f.path}
                  onClick={() => choose(() => onPickFile(f))}
                  className={cn(
                    "flex w-full items-baseline gap-2 rounded-sm px-2 py-1.5 text-left hover:bg-surface-2",
                    label === f.path && "bg-violet/10",
                  )}
                >
                  <span className="shrink-0 font-mono text-[12px] font-medium">{baseName(f.path)}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-[10.5px] text-subtle">{dirName(f.path)}</span>
                </button>
              ))
            )}
            {(tab === "endpoints" ? endpointMatches.length : fileMatches.length) > PICKER_LIMIT ? (
              <p className="px-2 py-2 text-center text-[11px] text-subtle">
                Showing the first {PICKER_LIMIT}. Type to narrow the list.
              </p>
            ) : null}
          </div>
          {tab === "files" ? (
            <p className="border-t border-border px-3 py-2 text-[11px] text-subtle">Test files aren't listed — traces skip them.</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// --- trace view ----------------------------------------------------------------------

type NotesQuery = ReturnType<typeof useTraceNotes>;

interface Row {
  hop: TraceHop;
  /** Per ancestor column: does that ancestor have a later sibling (draw a line through)? */
  guides: boolean[];
  last: boolean;
}

/** Nests the breadth-first hop list by `parent_index` (depth-first, siblings in
 * index order) and records which guide lines each row needs. */
function buildRows(hops: TraceHop[]): Row[] {
  const children = new Map<number, TraceHop[]>();
  for (const h of hops) {
    if (h.parent_index !== null) children.set(h.parent_index, [...(children.get(h.parent_index) ?? []), h]);
  }
  const rows: Row[] = [];
  const walk = (hop: TraceHop, guides: boolean[], last: boolean) => {
    rows.push({ hop, guides, last });
    const kids = (children.get(hop.index) ?? []).sort((a, b) => a.index - b.index);
    const next = hop.parent_index === null ? guides : [...guides, !last];
    kids.forEach((k, i) => walk(k, next, i === kids.length - 1));
  };
  const root = hops.find((h) => h.parent_index === null);
  if (root) walk(root, [], true);
  return rows;
}

function TraceView({ data, notes }: { data: TracerRead; notes: NotesQuery }) {
  const [selected, setSelected] = useState(0);
  const rows = useMemo(() => buildRows(data.hops), [data.hops]);
  const hop = data.hops.find((h) => h.index === selected) ?? data.hops[0]!;

  return (
    <>
      <Summary data={data} />
      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_400px]">
        <Panel className="h-fit overflow-hidden">
          <PanelHeader
            title={data.start?.label ?? "Trace"}
            subtitle={data.start?.kind === "endpoint" ? `Declared in ${data.start.file_path}` : "Traced from this file"}
            icon={<RouteIcon className="size-3.5" />}
          />
          <ol className="p-2" aria-label="Files on the traced path">
            {rows.map((row) => (
              <TreeRow key={row.hop.index} row={row} active={row.hop.index === hop.index} onSelect={() => setSelected(row.hop.index)} />
            ))}
          </ol>
        </Panel>

        <div className="xl:sticky xl:top-4 xl:self-start">
          <HopDetail key={hop.index} data={data} hop={hop} notes={notes} onSelect={setSelected} />
        </div>
      </div>
    </>
  );
}

/** The five layers a request path moves through, lit where the trace reaches
 * them — so where it ends is visible at a glance. */
function LayerLane({ data }: { data: TracerRead }) {
  const counts = new Map<TraceLayer, number>();
  for (const h of data.hops) counts.set(h.layer, (counts.get(h.layer) ?? 0) + 1);
  const stages: TraceLayer[] = counts.has("other") ? [...LAYER_ORDER, "other"] : [...LAYER_ORDER];

  return (
    <ol className="flex flex-wrap items-stretch gap-1.5" aria-label="Layers reached">
      {stages.map((layer) => {
        const n = counts.get(layer) ?? 0;
        const reached = n > 0;
        return (
          <li
            key={layer}
            className={cn(
              "flex min-w-[96px] items-center gap-2 rounded-md border px-2.5 py-1.5",
              reached ? "border-transparent" : "border-dashed border-border",
            )}
            style={reached ? { background: `color-mix(in oklch, ${LAYER_COLOR[layer]} 14%, transparent)` } : undefined}
          >
            <span
              className="size-2 shrink-0 rounded-full"
              style={{ background: reached ? LAYER_COLOR[layer] : "var(--border-strong)" }}
              aria-hidden
            />
            <span className={cn("text-[12px]", reached ? "font-medium text-foreground" : "text-subtle")}>{LAYER_LABEL[layer]}</span>
            <span className="ml-auto font-mono text-[11px] text-subtle">{reached ? n : "not reached"}</span>
          </li>
        );
      })}
    </ol>
  );
}

function Summary({ data }: { data: TracerRead }) {
  const services = useMemo(() => {
    const seen = new Map<string, string>();
    for (const h of data.hops) for (const s of h.external_services) seen.set(s.service_id, s.label);
    return [...seen.values()];
  }, [data.hops]);
  const deepest = [...data.layers_reached].reverse().find((l) => l !== "other") ?? data.layers_reached[0];
  const start = data.start;

  return (
    <Panel ambient>
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <LayerLane data={data} />
          <span className="ml-auto flex flex-wrap items-center gap-1.5">
            {data.reaches_data ? (
              <Tag tone="success">Reaches data</Tag>
            ) : deepest ? (
              <Tag>Ends at {LAYER_LABEL[deepest].toLowerCase()}</Tag>
            ) : null}
            {data.truncated ? <Tag tone="warning">Cut off at {data.max_hops} files</Tag> : null}
          </span>
        </div>

        {services.length > 0 ? (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[12px] text-muted-foreground">Reaches</span>
            {services.map((s) => (
              <Tag key={s} tone="violet">{s}</Tag>
            ))}
          </div>
        ) : null}

        <div className="space-y-0.5 text-[11.5px] text-subtle">
          <p>
            Built from file imports, so it shows which files this request can reach, not which functions run. Layers are
            guessed from folder and file names.
          </p>
          {start && start.endpoints_in_file > 1 ? (
            <p>
              <span className="font-mono">{baseName(start.file_path)}</span> declares {start.endpoints_in_file} endpoints, and
              all of them share this trace.
            </p>
          ) : null}
          {data.truncated ? (
            <p className="text-warning">
              The trace stops at its limit ({data.max_hops} files, {data.max_depth} levels deep); more files are reachable than shown.
            </p>
          ) : null}
        </div>
      </div>
    </Panel>
  );
}

function TreeRow({ row, active, onSelect }: { row: Row; active: boolean; onSelect: () => void }) {
  const { hop, guides, last } = row;
  const color = LAYER_COLOR[hop.layer];
  const hasMeta = hop.fn !== null || hop.external_services.length > 0 || hop.via.length > 0;

  return (
    <li>
      <div className="flex">
        {guides.map((through, i) => (
          <span key={i} aria-hidden className="relative w-5 shrink-0 self-stretch">
            {through ? <span className="absolute top-0 left-2.5 h-full border-l border-border-strong" /> : null}
          </span>
        ))}
        {hop.parent_index !== null ? (
          <span aria-hidden className="relative w-5 shrink-0 self-stretch">
            <span className={cn("absolute top-0 left-2.5 border-l border-border-strong", last ? "h-[19px]" : "h-full")} />
            <span className="absolute top-[19px] left-2.5 w-2.5 border-t border-border-strong" />
          </span>
        ) : null}
        <button
          type="button"
          onClick={onSelect}
          aria-current={active}
          className={cn(
            "focus-ring my-0.5 min-w-0 flex-1 rounded-md border-l-[3px] px-3 py-2 text-left transition-colors",
            active ? "bg-violet/12" : "hover:bg-surface-2",
          )}
          style={{ borderLeftColor: color }}
        >
          <div className="flex items-baseline gap-2">
            <span className="w-[74px] shrink-0 text-[11.5px] font-medium" style={{ color }}>
              {LAYER_LABEL[hop.layer]}
            </span>
            <span className="min-w-0 font-mono text-[12.5px] font-medium break-all sm:shrink-0 sm:break-normal">
              {baseName(hop.file_path)}
            </span>
            <span className="hidden min-w-0 flex-1 truncate font-mono text-[10.5px] text-subtle sm:block">
              {dirName(hop.file_path)}
            </span>
          </div>
          {hasMeta ? (
            <div className="mt-1.5 flex flex-wrap items-center gap-1 sm:pl-[82px]">
              {hop.fn ? <Tag tone="violet" mono>{hop.fn}</Tag> : null}
              {hop.external_services.map((s) => (
                <Tag key={s.service_id}>{s.label}</Tag>
              ))}
              {hop.via.length > 0 ? (
                <span className="text-[11px] text-subtle">through {hop.via.map(baseName).join(" › ")}</span>
              ) : null}
            </div>
          ) : null}
        </button>
      </div>
    </li>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
  return <p className="mb-1.5 text-[11.5px] font-medium text-muted-foreground">{children}</p>;
}

function HopDetail({
  data,
  hop,
  notes,
  onSelect,
}: {
  data: TracerRead;
  hop: TraceHop;
  notes: NotesQuery;
  onSelect: (index: number) => void;
}) {
  const color = LAYER_COLOR[hop.layer];
  const parent = hop.parent_index !== null ? data.hops.find((h) => h.index === hop.parent_index) : null;
  const hopByPath = useMemo(() => new Map(data.hops.map((h) => [h.file_path, h])), [data.hops]);
  const extraSymbols = hop.symbol_count - hop.symbols.length;

  return (
    <Panel>
      <PanelHeader
        title={baseName(hop.file_path)}
        subtitle={dirName(hop.file_path) || "repository root"}
        icon={<FileCode2 className="size-3.5" />}
        actions={
          <Link
            to="/app/codebase"
            search={{ file: hop.file_path }}
            className="focus-ring inline-flex items-center gap-1 text-[12px] text-violet-soft hover:underline"
          >
            Open in Codebase <ExternalLink className="size-3" />
          </Link>
        }
      />
      <div className="space-y-4 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className="inline-flex items-center gap-1.5 rounded-sm px-1.5 py-0.5 text-[11.5px] font-medium"
            style={{ color, background: `color-mix(in oklch, ${color} 14%, transparent)` }}
          >
            <span className="size-1.5 rounded-full" style={{ background: color }} aria-hidden />
            {LAYER_LABEL[hop.layer]}
          </span>
          <span className="font-mono text-[11.5px] text-subtle">{hop.loc.toLocaleString()} lines</span>
          {hop.fn ? <Tag tone="violet" mono>{hop.fn}</Tag> : null}
        </div>
        {hop.fn ? (
          <p className="-mt-2 text-[11.5px] text-subtle">The handler named in this route's declaration.</p>
        ) : null}

        {parent ? (
          <div>
            <SectionLabel>Reached from</SectionLabel>
            <button
              type="button"
              onClick={() => onSelect(parent.index)}
              className="focus-ring font-mono text-[12px] text-violet-soft hover:underline"
            >
              {baseName(parent.file_path)}
            </button>
            {hop.via.length > 0 ? (
              <p className="mt-0.5 text-[11.5px] text-subtle">
                through <span className="font-mono">{hop.via.map(baseName).join(" › ")}</span>, which{" "}
                {hop.via.length === 1 ? "isn't" : "aren't"} a layer of its own.
              </p>
            ) : null}
          </div>
        ) : null}

        <div>
          <SectionLabel>Declares</SectionLabel>
          {hop.symbols.length > 0 ? (
            <div className="flex flex-wrap gap-1">
              {hop.symbols.map((s, i) => (
                <Tag key={`${s}-${i}`} mono>{s}</Tag>
              ))}
              {extraSymbols > 0 ? <span className="self-center text-[11.5px] text-subtle">+{extraSymbols} more</span> : null}
            </div>
          ) : (
            <p className="text-[12px] text-subtle">No functions or classes recorded in this file.</p>
          )}
        </div>

        <div>
          <SectionLabel>Imports</SectionLabel>
          {hop.deps.length > 0 ? (
            <div className="flex flex-wrap gap-1">
              {hop.deps.map((d) => {
                const inTrace = hopByPath.get(d);
                return inTrace ? (
                  <button
                    key={d}
                    type="button"
                    title={`${d} — on this trace`}
                    onClick={() => onSelect(inTrace.index)}
                    className="focus-ring"
                  >
                    <Tag tone="violet" mono>{baseName(d)}</Tag>
                  </button>
                ) : (
                  <span key={d} title={d}>
                    <Tag mono>{baseName(d)}</Tag>
                  </span>
                );
              })}
            </div>
          ) : (
            <p className="text-[12px] text-subtle">Imports no other files in this repository.</p>
          )}
          <p className="mt-1 text-[11px] text-subtle">
            Highlighted imports are stops on this trace. The rest are shared utilities or config, or files the trace doesn't add.
          </p>
        </div>

        {hop.external_services.length > 0 ? (
          <div>
            <SectionLabel>Reaches</SectionLabel>
            <ul className="space-y-1.5">
              {hop.external_services.map((s) => (
                <li key={s.service_id} className="text-[12px]">
                  <span className="font-medium">{s.label}</span>{" "}
                  <span className="text-subtle">
                    via <span className="font-mono">{s.packages.join(", ")}</span>
                    {s.via.length > 0 ? (
                      <>
                        , imported by <span className="font-mono">{baseName(s.via[s.via.length - 1]!)}</span>
                        {s.via.length > 1 ? (
                          <>
                            , reached through <span className="font-mono">{s.via.slice(0, -1).map(baseName).join(" › ")}</span>
                          </>
                        ) : (
                          <>, which this file imports</>
                        )}
                      </>
                    ) : (
                      ", imported by this file"
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <HopNote hop={hop} notes={notes} />
      </div>
    </Panel>
  );
}

function HopNote({ hop, notes }: { hop: TraceHop; notes: NotesQuery }) {
  const waiting = notes.isLoading || (notes.isFetching && !notes.data);
  const written = notes.data?.notes?.find((n) => n.index === hop.index)?.note ?? null;
  const failed = !waiting && (notes.isError || (notes.data !== undefined && notes.data.notes === null));

  return (
    <div className="rounded-md border border-violet/22 bg-violet/[0.07] p-3">
      <p className="mb-1.5 flex items-center gap-1.5 text-[11.5px] font-medium text-violet-soft">
        <span className={cn("inline-block size-1.5 rounded-full bg-violet", waiting && "pulse-node")} />
        AI note
      </p>
      {waiting ? (
        <div className="space-y-1.5" aria-label="Writing a note">
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-3/4" />
        </div>
      ) : written ? (
        <>
          <p className="text-[12.5px] leading-relaxed text-secondary-foreground">{written}</p>
          <p className="mt-1.5 text-[11px] text-subtle">
            Written from file names and imports only. It can't see what the code does, so check it against the file.
          </p>
        </>
      ) : failed ? (
        <p className="text-[12px] text-muted-foreground">
          Couldn't write notes for this trace right now.{" "}
          <button type="button" onClick={() => void notes.refetch()} className="focus-ring text-violet-soft hover:underline">
            Retry
          </button>
        </p>
      ) : (
        <p className="text-[12px] text-muted-foreground">No note was written for this file.</p>
      )}
    </div>
  );
}
