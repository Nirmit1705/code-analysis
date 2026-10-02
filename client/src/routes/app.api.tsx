import { createFileRoute, Link } from "@tanstack/react-router";
import { Fragment, useMemo, useState, type ReactNode } from "react";
import { ArrowRight, ChevronDown, ChevronRight, ExternalLink, Plug, Search } from "lucide-react";

import { Panel, PanelHeader, Tag, severityTone } from "@/components/ui-kit";
import { useActiveRepositoryId } from "@/lib/active-repo";
import {
  FRAMEWORK_LABEL,
  useApiEndpointDetail,
  useApiEndpoints,
  type ApiEndpointRead,
  type ApiReferenceRead,
  type ApiTouches,
  type CodeSnippet,
} from "@/lib/queries/apiReference";
import { useRepositories } from "@/lib/queries/repos";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/api")({
  head: () => ({
    meta: [
      { title: "API Reference — CodeLens AI" },
      { name: "description", content: "Every endpoint discovered in the codebase, with its handler, middleware, source and what its file depends on." },
      { property: "og:title", content: "API Reference — CodeLens AI" },
      { property: "og:description", content: "A reference generated from route handlers, not from hand-written specs." },
    ],
  }),
  component: ApiReference,
});

const methodTone: Record<string, string> = {
  GET: "text-success border-success/30 bg-success/10",
  POST: "text-info border-info/30 bg-info/10",
  PUT: "text-warning border-warning/30 bg-warning/10",
  PATCH: "text-warning border-warning/30 bg-warning/10",
  DELETE: "text-danger border-danger/30 bg-danger/10",
};
const neutralMethodTone = "text-muted-foreground border-border bg-surface-2";

function MethodBadge({ method, large }: { method: string; large?: boolean }) {
  return (
    <span
      className={cn(
        "rounded border font-mono",
        large ? "px-2 py-1 text-[11px]" : "px-1.5 py-0.5 text-[10px]",
        methodTone[method] ?? neutralMethodTone,
      )}
    >
      {method}
    </span>
  );
}

function shortSha(sha: string): string {
  return sha.slice(0, 7);
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function ApiReference() {
  const [q, setQ] = useState("");
  const [method, setMethod] = useState<string | null>(null);
  const [newOnly, setNewOnly] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());

  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const reference = useApiEndpoints(activeRepositoryId);
  const data = reference.data;
  const endpoints = useMemo(() => (data?.analyzed ? data.endpoints : []), [data]);

  const methodCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const e of endpoints) counts.set(e.method, (counts.get(e.method) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [endpoints]);

  const groupCount = useMemo(() => new Set(endpoints.map((e) => e.group)).size, [endpoints]);
  const frameworks = useMemo(
    () => [...new Set(endpoints.map((e) => FRAMEWORK_LABEL[e.framework]))].sort(),
    [endpoints],
  );

  const needle = q.trim().toLowerCase();
  const filtering = needle !== "" || method !== null || newOnly;
  const list = useMemo(
    () =>
      endpoints.filter(
        (e) =>
          (method === null || e.method === method) &&
          (!newOnly || e.change === "new") &&
          (needle === "" ||
            `${e.path} ${e.service} ${e.method} ${e.handler?.name ?? ""} ${(e.middleware ?? []).join(" ")}`
              .toLowerCase()
              .includes(needle)),
      ),
    [endpoints, method, newOnly, needle],
  );
  const groups = useMemo(() => {
    const byGroup = new Map<string, ApiEndpointRead[]>();
    for (const e of list) byGroup.set(e.group, [...(byGroup.get(e.group) ?? []), e]);
    return [...byGroup.entries()];
  }, [list]);

  // A filter can hide the selected row; fall back to the first visible one
  // rather than showing a detail panel for something not in the list.
  const ep = list.find((e) => e.id === selectedId) ?? list[0] ?? null;

  const toggleGroup = (group: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });

  const subtitle =
    endpoints.length > 0
      ? `${endpoints.length} endpoint${endpoints.length === 1 ? "" : "s"} in ${groupCount} resource group${groupCount === 1 ? "" : "s"} · ${frameworks.join(", ")} · extracted from route declarations.`
      : "Endpoints extracted from route declarations in the codebase.";

  return (
    <div className="mx-auto max-w-[1440px] space-y-3 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-[17px] font-semibold tracking-tight">API Reference</h1>
          <p className="text-[12px] text-muted-foreground">{subtitle}</p>
        </div>
        {endpoints.length > 0 ? (
          <div className="ml-auto flex items-center gap-2 rounded-md border border-input bg-background px-2 py-1.5">
            <Search className="size-3.5 text-subtle" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Filter by path, handler or middleware"
              aria-label="Filter endpoints"
              className="w-64 bg-transparent text-[12.5px] outline-none placeholder:text-subtle"
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
      ) : reference.isLoading ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-subtle">Loading endpoints…</p>
        </Panel>
      ) : reference.isError || !data ? (
        <Panel>
          <p className="p-8 text-center text-[13px] text-danger">Failed to load API endpoints.</p>
        </Panel>
      ) : !data.analyzed ? (
        <Panel>
          <div className="p-10 text-center">
            <p className="text-[13px] text-subtle">This repository hasn't been analyzed yet.</p>
            <Link to="/onboarding" className="focus-ring mt-2 inline-block text-[12.5px] text-violet-soft hover:underline">
              Run the first analysis
            </Link>
          </div>
        </Panel>
      ) : endpoints.length === 0 ? (
        <Panel>
          <p className="p-10 text-center text-[13px] text-subtle">
            No API endpoints were detected. Routes are recognised for Express, Spring Boot, FastAPI and Flask.
          </p>
        </Panel>
      ) : (
        <>
          <ChangeStrip data={data} />

          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by method">
            <Chip label="All" count={endpoints.length} active={method === null && !newOnly} onClick={() => { setMethod(null); setNewOnly(false); }} />
            {methodCounts.map(([m, n]) => (
              <Chip key={m} label={m} count={n} active={method === m} onClick={() => setMethod(method === m ? null : m)} />
            ))}
            {data.new_count > 0 ? (
              <Chip label="New" count={data.new_count} active={newOnly} onClick={() => setNewOnly(!newOnly)} />
            ) : null}
          </div>

          <div className="grid gap-3 xl:grid-cols-[420px_minmax(0,1fr)]">
            <Panel className="h-fit overflow-hidden">
              <PanelHeader
                title="Endpoints"
                subtitle={`${list.length} of ${endpoints.length} shown`}
                icon={<Plug className="size-3.5" />}
              />
              {list.length === 0 ? (
                <p className="p-6 text-center text-[12.5px] text-subtle">No endpoints match this filter.</p>
              ) : (
                <div className="max-h-[calc(100vh-15rem)] overflow-y-auto">
                  {groups.map(([group, items]) => {
                    const open = filtering || !collapsed.has(group);
                    return (
                      <section key={group} className="border-b border-border last:border-b-0">
                        <button
                          type="button"
                          onClick={() => toggleGroup(group)}
                          aria-expanded={open}
                          className="focus-ring flex w-full items-center gap-2 bg-surface-2/60 px-3 py-2 text-left hover:bg-surface-2"
                        >
                          {open ? <ChevronDown className="size-3.5 text-subtle" /> : <ChevronRight className="size-3.5 text-subtle" />}
                          <span className="min-w-0 flex-1 truncate font-mono text-[11.5px]">{group}</span>
                          <GroupMix items={items} />
                          <span className="font-mono text-[10.5px] text-subtle">{items.length}</span>
                        </button>
                        {open ? (
                          <ul className="divide-y divide-border">
                            {items.map((e) => (
                              <li key={e.id}>
                                <button
                                  type="button"
                                  onClick={() => setSelectedId(e.id)}
                                  aria-current={ep?.id === e.id}
                                  className={cn(
                                    "focus-ring flex w-full items-center gap-2 px-3 py-2 text-left transition-colors",
                                    ep?.id === e.id ? "bg-violet/12" : "hover:bg-surface-2",
                                  )}
                                >
                                  <span className="w-14 shrink-0">
                                    <MethodBadge method={e.method} />
                                  </span>
                                  <span className="min-w-0 flex-1 truncate font-mono text-[11.5px]">
                                    {e.path.startsWith(group) && group !== "/" ? e.path.slice(group.length) || "/" : e.path}
                                  </span>
                                  {e.change === "new" ? <Tag tone="success" caps>new</Tag> : null}
                                </button>
                              </li>
                            ))}
                          </ul>
                        ) : null}
                      </section>
                    );
                  })}
                </div>
              )}
            </Panel>

            {ep && activeRepositoryId ? <EndpointDetail key={ep.id} repositoryId={activeRepositoryId} ep={ep} /> : null}
          </div>
        </>
      )}
    </div>
  );
}

function Chip({ label, count, active, onClick }: { label: string; count: number; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "focus-ring rounded-md border px-2.5 py-1 font-mono text-[11px] transition-colors",
        active ? "border-violet bg-violet/14 text-foreground" : "border-border text-muted-foreground hover:bg-surface-2",
      )}
    >
      {label} <span className="text-subtle">{count}</span>
    </button>
  );
}

function GroupMix({ items }: { items: ApiEndpointRead[] }) {
  const counts = new Map<string, number>();
  for (const e of items) counts.set(e.method, (counts.get(e.method) ?? 0) + 1);
  return (
    <span className="flex items-center gap-1">
      {[...counts.entries()].sort().map(([m, n]) => (
        <span key={m} className={cn("rounded border px-1 font-mono text-[9.5px]", methodTone[m] ?? neutralMethodTone)}>
          {m.slice(0, 3)} {n}
        </span>
      ))}
    </span>
  );
}

/** What changed in the API surface versus the baseline run (earlier completed
 * run, same branch, different commit). Hidden entirely when there is nothing
 * to compare against, so it never implies "no changes" without a baseline. */
function ChangeStrip({ data }: { data: ApiReferenceRead }) {
  const [open, setOpen] = useState(false);
  if (data.baseline === null) {
    return (
      <p className="text-[11.5px] text-subtle">
        No earlier analysis at a different commit on this branch, so there is nothing to compare the API surface against yet.
      </p>
    );
  }
  const none = data.new_count === 0 && data.removed.length === 0;
  return (
    <Panel>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5 text-[12px]">
        <span className="text-muted-foreground">
          vs <span className="font-mono text-foreground">{shortSha(data.baseline.commit_sha)}</span> ({formatDate(data.baseline.completed_at)})
        </span>
        {none ? (
          <span className="text-muted-foreground">No API changes.</span>
        ) : (
          <>
            <span><span className="font-mono text-success">+{data.new_count}</span> new</span>
            <span><span className="font-mono text-danger">−{data.removed.length}</span> removed</span>
            {data.removed.length > 0 ? (
              <button
                type="button"
                onClick={() => setOpen(!open)}
                aria-expanded={open}
                className="focus-ring ml-auto text-violet-soft hover:underline"
              >
                {open ? "Hide removed" : "Show removed"}
              </button>
            ) : null}
          </>
        )}
      </div>
      {open && data.removed.length > 0 ? (
        <ul className="divide-y divide-border border-t border-border">
          {data.removed.map((r) => (
            <li key={`${r.method} ${r.path}`} className="flex items-center gap-2 px-4 py-2">
              <MethodBadge method={r.method} />
              <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] line-through decoration-danger/50">{r.path}</span>
              <span className="truncate font-mono text-[10.5px] text-subtle">{r.file_path}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}

/** The path with each parameter segment highlighted. */
function PathWithParams({ path }: { path: string }) {
  const parts = path.split(/(:[A-Za-z_]\w*|\{[^}]+\}|<[^>]+>)/g);
  return (
    <span className="min-w-0 break-all font-mono text-[15px]">
      {parts.map((part, i) =>
        /^(:[A-Za-z_]\w*|\{[^}]+\}|<[^>]+>)$/.test(part) ? (
          <span key={i} className="rounded bg-violet/14 px-0.5 text-violet-soft">{part}</span>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </span>
  );
}

function EndpointDetail({ repositoryId, ep }: { repositoryId: string; ep: ApiEndpointRead }) {
  const detail = useApiEndpointDetail(repositoryId, ep.id);
  const handler = ep.handler;
  const d = detail.data;

  return (
    <div className="min-w-0 space-y-3">
      <Panel ambient>
        <div className="flex flex-wrap items-center gap-3 p-4">
          <MethodBadge method={ep.method} large />
          <PathWithParams path={ep.path} />
          <span className="ml-auto flex flex-wrap items-center gap-1.5">
            {ep.change === "new" ? <Tag tone="success" caps>new</Tag> : null}
            <Tag tone="violet">{ep.service}</Tag>
            <Tag mono>{FRAMEWORK_LABEL[ep.framework]}</Tag>
            <Link
              to="/app/tracer"
              search={{ endpoint: `${ep.method} ${ep.path}` }}
              className="focus-ring inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[12px] text-violet-soft hover:border-border-strong hover:bg-surface-2"
            >
              Trace flow <ArrowRight className="size-3" />
            </Link>
          </span>
        </div>
      </Panel>

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel>
          <PanelHeader title="Request path" subtitle="Parsed from the route's path" />
          <div className="p-4">
            {ep.path_params.length === 0 ? (
              <p className="text-[12px] text-muted-foreground">No path parameters.</p>
            ) : (
              <ul className="divide-y divide-border">
                {ep.path_params.map((p) => (
                  <li key={p.name} className="flex items-center justify-between gap-3 py-1.5">
                    <span className="font-mono text-[12.5px]">{p.name}</span>
                    <span className="font-mono text-[11px] text-subtle">{p.type ?? "type not declared"}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Middleware → handler" subtitle="As written on the route" />
          <div className="space-y-2 p-4">
            <MiddlewareChain ep={ep} />
          </div>
        </Panel>
      </div>

      <Panel>
        <PanelHeader
          title="Handler"
          {...(handler ? { subtitle: handler.resolved ? "Resolved to a symbol" : "Reference only" } : {})}
        />
        <div className="space-y-2 p-4">
          {handler ? (
            <>
              <p className="break-all font-mono text-[13px]">{handler.name}</p>
              {handler.resolved && handler.file_path ? (
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                  <p className="break-all font-mono text-[11.5px] text-muted-foreground">
                    {handler.file_path}
                    {handler.line_start !== null ? `:${handler.line_start}` : ""}
                    {handler.line_end !== null && handler.line_end !== handler.line_start ? `–${handler.line_end}` : ""}
                  </p>
                  <OpenInCodebase label="Open handler" file={handler.file_path} lineStart={handler.line_start} lineEnd={handler.line_end} />
                </div>
              ) : (
                <p className="text-[12px] text-muted-foreground">
                  {handler.name === "<inline>"
                    ? "Not a named function reference (an inline callback or a wrapped expression), so there is no definition to point at."
                    : "Referenced by name; its definition was not resolved to a file in this analysis."}
                </p>
              )}
            </>
          ) : (
            <p className="text-[12px] text-muted-foreground">No handler was recorded for this route.</p>
          )}
        </div>
      </Panel>

      <Panel>
        <PanelHeader
          title="Source"
          subtitle={`${ep.file_path}${ep.line !== null ? `:${ep.line}` : ""}`}
          actions={<OpenInCodebase label="Open in Codebase" file={ep.file_path} lineStart={ep.line} lineEnd={ep.line_end ?? ep.line} />}
        />
        {detail.isLoading ? (
          <p className="p-4 text-[12px] text-subtle">Loading source…</p>
        ) : detail.isError || !d ? (
          <p className="p-4 text-[12px] text-danger">Failed to load the source for this endpoint.</p>
        ) : (
          <div className="space-y-3 p-4">
            {d.declaration ? <CodeBlock label="Route declaration" snippet={d.declaration} /> : null}
            {d.handler_code ? <CodeBlock label={`Handler · ${handler?.name ?? ""}`} snippet={d.handler_code} /> : null}
            {!d.declaration && !d.handler_code ? (
              <p className="text-[12px] text-muted-foreground">{d.source_note ?? "No source is available for this endpoint."}</p>
            ) : null}
          </div>
        )}
      </Panel>

      <Panel>
        <PanelHeader
          title="What this route's file touches"
          subtitle={
            d
              ? d.touches.routes_in_file > 1
                ? `File-level facts, shared by all ${d.touches.routes_in_file} routes in ${d.touches.route_file}`
                : `File-level facts for ${d.touches.route_file}`
              : "File-level facts"
          }
        />
        {detail.isLoading ? (
          <p className="p-4 text-[12px] text-subtle">Loading…</p>
        ) : d ? (
          <Touches touches={d.touches} />
        ) : (
          <p className="p-4 text-[12px] text-danger">Failed to load.</p>
        )}
      </Panel>
    </div>
  );
}

function MiddlewareChain({ ep }: { ep: ApiEndpointRead }) {
  const handlerLabel = ep.handler?.name ?? "handler";
  if (ep.middleware === null) {
    return (
      <p className="text-[12px] text-muted-foreground">
        {ep.framework === "express"
          ? "Not recorded for this analysis run — re-analyze to see the middleware named on this route."
          : `Middleware isn't extracted for ${FRAMEWORK_LABEL[ep.framework]} routes.`}
      </p>
    );
  }
  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5">
        {ep.middleware.map((m, i) => (
          <Fragment key={`${m}-${i}`}>
            <Tag mono>{m}</Tag>
            <ArrowRight className="size-3 text-subtle" />
          </Fragment>
        ))}
        <Tag tone="violet" mono>{handlerLabel}</Tag>
      </div>
      <p className="text-[11px] text-subtle">
        {ep.middleware.length === 0 ? "No middleware is named on this route. " : ""}
        Only middleware written on the route itself is shown — anything applied through <span className="font-mono">app.use()</span> or{" "}
        <span className="font-mono">router.use()</span> isn't included, so this says nothing about whether the route is protected.
      </p>
    </>
  );
}

function CodeBlock({ label, snippet }: { label: string; snippet: CodeSnippet }) {
  const lines = snippet.code.split("\n");
  return (
    <div className="min-w-0">
      <p className="mb-1 flex flex-wrap items-baseline gap-x-2 text-[11px] text-subtle">
        <span className="font-medium text-muted-foreground">{label}</span>
        <span className="font-mono">
          {snippet.file_path}:{snippet.start_line}–{snippet.end_line}
        </span>
      </p>
      <div className="overflow-x-auto rounded-md border border-border bg-background py-2 font-mono text-[11.5px] leading-relaxed">
        {lines.map((text, i) => (
          <div key={i} className="flex">
            <span className="w-12 shrink-0 select-none pr-3 text-right text-subtle">{snippet.start_line + i}</span>
            <span className="whitespace-pre pr-4">{text}</span>
          </div>
        ))}
      </div>
      {snippet.truncated ? (
        <p className="mt-1 text-[11px] text-subtle">Showing the first {lines.length} lines — open in Codebase for the rest.</p>
      ) : null}
    </div>
  );
}

function Touches({ touches: t }: { touches: ApiTouches }) {
  return (
    <div className="grid gap-x-6 gap-y-4 p-4 lg:grid-cols-2">
      <Block title="Imports">
        {t.imports.length === 0 ? <None>No internal imports.</None> : (
          <ul className="space-y-1">
            {t.imports.map((f) => (
              <li key={f.file_path} className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate font-mono text-[11.5px]">{f.file_path}</span>
                {f.service ? <span className="shrink-0 text-[10.5px] text-subtle">{f.service}</span> : null}
              </li>
            ))}
          </ul>
        )}
      </Block>

      <Block title="External services">
        {t.external.length === 0 ? <None>No recognised external services.</None> : (
          <ul className="space-y-1.5">
            {t.external.map((s) => (
              <li key={s.label} className="flex flex-wrap items-center gap-1.5">
                <Tag tone={s.kind === "data" ? "success" : s.kind === "cache" ? "info" : "violet"}>{s.label}</Tag>
                <span className="font-mono text-[11px] text-subtle">{s.packages.join(", ")}</span>
              </li>
            ))}
          </ul>
        )}
      </Block>

      <Block title="Imported by">
        {t.used_by_total === 0 ? <None>Nothing imports this file.</None> : (
          <ul className="space-y-1">
            {t.used_by.map((f) => (
              <li key={f.file_path} className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate font-mono text-[11.5px]">{f.file_path}</span>
                {f.service ? <span className="shrink-0 text-[10.5px] text-subtle">{f.service}</span> : null}
              </li>
            ))}
            {t.used_by_total > t.used_by.length ? (
              <li className="text-[11px] text-subtle">and {t.used_by_total - t.used_by.length} more</li>
            ) : null}
          </ul>
        )}
      </Block>

      <Block title="Health of this file">
        <div className="flex flex-wrap items-center gap-1.5">
          <Tag mono>{t.loc} lines</Tag>
          {t.complexity_score !== null ? <Tag mono>avg complexity {t.complexity_score}</Tag> : null}
          {t.in_cycle ? <Tag tone="danger">in a {t.cycle_size}-file circular dependency</Tag> : <Tag tone="success">not in a cycle</Tag>}
        </div>
        {t.debt.length === 0 ? (
          <p className="mt-2 text-[12px] text-muted-foreground">No debt findings on this file.</p>
        ) : (
          <ul className="mt-2 space-y-1.5">
            {t.debt.map((i, idx) => (
              <li key={idx} className="flex items-start gap-2">
                <Tag tone={severityTone(i.severity)} caps>{i.severity}</Tag>
                <span className="min-w-0 flex-1 text-[12px] text-muted-foreground">{i.description}</span>
              </li>
            ))}
          </ul>
        )}
      </Block>
    </div>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <h3 className="label-xs mb-1.5">{title}</h3>
      {children}
    </div>
  );
}

function None({ children }: { children: ReactNode }) {
  return <p className="text-[12px] text-muted-foreground">{children}</p>;
}

function OpenInCodebase({
  label,
  file,
  lineStart,
  lineEnd,
}: {
  label: string;
  file: string;
  lineStart: number | null;
  lineEnd: number | null;
}) {
  return (
    <Link
      to="/app/codebase"
      search={{
        file,
        ...(lineStart !== null ? { line_start: lineStart } : {}),
        ...(lineEnd !== null ? { line_end: lineEnd } : {}),
      }}
      className="focus-ring inline-flex items-center gap-1 text-[12px] text-violet-soft hover:underline"
    >
      {label} <ExternalLink className="size-3" />
    </Link>
  );
}
