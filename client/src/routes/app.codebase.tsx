import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { ChevronRight, ChevronDown, FileCode2, Search, Sparkles, Folder } from "lucide-react";
import { z } from "zod";

import { AiNote, Panel, PanelHeader, ScoreBar, Tag, severityTone } from "@/components/ui-kit";
import { useActiveRepositoryId } from "@/lib/active-repo";
import {
  buildFileTree,
  fileModule,
  useFileContent,
  useFileInsight,
  useFileTree,
  type CodebaseTreeNode,
  type FileTreeEntry,
} from "@/lib/queries/codebase";
import { useRepositories } from "@/lib/queries/repos";
import { cn } from "@/lib/utils";

// `file` deep-links a specific file by its real `File.path` (not `File.id` —
// a re-analysis always creates fresh `File` rows with new ids, per
// `file_insight_service.py`'s own precedent, so a path survives a
// re-analysis and an id wouldn't). `line_start`/`line_end` highlight and
// scroll to that exact range — the AI Assistant's citation chips are the
// first real producer of this link shape (`Reference.file_path`/
// `line_start`/`line_end` already carried this data; it just had nowhere to
// go before). `z.coerce` mirrors `onboarding.tsx`'s own defensive stance —
// TanStack Router's default search parser already JSON-parses numeric-looking
// values, but coercing explicitly here is cheap insurance against either shape.
const searchSchema = z.object({
  file: z.coerce.string().optional(),
  line_start: z.coerce.number().optional(),
  line_end: z.coerce.number().optional(),
});

export const Route = createFileRoute("/app/codebase")({
  validateSearch: searchSchema,
  head: () => ({
    meta: [
      { title: "Codebase Explorer — Substrate" },
      {
        name: "description",
        content:
          "IDE-style codebase explorer with file tree, syntax-aware code viewer and an AI insights panel per file.",
      },
      { property: "og:title", content: "Codebase Explorer — Substrate" },
      {
        property: "og:description",
        content:
          "Read any file alongside its purpose, dependencies, dependents, complexity and related APIs.",
      },
    ],
  }),
  component: Codebase,
});

// Real language values off `File.language` (`app/analysis/language_detect.py`)
// — a display-name map, same purpose as `dashboard.ts`'s own
// `_LANGUAGE_DISPLAY_NAMES`. A `null` language (a file outside Phase 3's
// parsed set — lockfiles, markdown, config, binaries, …) renders no tag at
// all rather than a fabricated "Unknown" label.
const LANGUAGE_LABELS: Record<string, string> = {
  javascript: "JavaScript",
  typescript: "TypeScript",
  tsx: "TSX",
  python: "Python",
  java: "Java",
};

const KEYWORDS =
  /\b(import|from|export|class|const|await|async|return|throw|new|private|readonly|constructor|if|this)\b/g;

function highlight(line: string) {
  if (line.trim().startsWith("//")) {
    return <span className="text-subtle italic">{line}</span>;
  }
  const parts: React.ReactNode[] = [];
  let last = 0;
  for (const m of line.matchAll(KEYWORDS)) {
    const i = m.index!;
    parts.push(line.slice(last, i));
    parts.push(
      <span key={`${i}-${m[0]}`} className="text-violet-soft">
        {m[0]}
      </span>,
    );
    last = i + m[0].length;
  }
  parts.push(line.slice(last));
  return parts;
}

function CenterMessage({ children, tone }: { children: React.ReactNode; tone?: "danger" }) {
  return (
    <Panel>
      <div className="p-10 text-center">
        <p className={cn("text-[13px]", tone === "danger" ? "text-danger" : "text-subtle")}>
          {children}
        </p>
      </div>
    </Panel>
  );
}

function Node({
  node,
  depth,
  selectedFileId,
  onSelect,
}: {
  node: CodebaseTreeNode;
  depth: number;
  selectedFileId: string | null;
  onSelect: (fileId: string) => void;
}) {
  const [open, setOpen] = useState(depth < 1);
  if (node.file) {
    const active = node.fileId !== undefined && node.fileId === selectedFileId;
    return (
      <button
        onClick={() => node.fileId && onSelect(node.fileId)}
        style={{ paddingLeft: depth * 12 + 8 }}
        className={cn(
          "flex w-full items-center gap-1.5 py-[3px] pr-2 text-left font-mono text-[11.5px] transition-colors",
          active ? "bg-violet/14 text-foreground" : "text-muted-foreground hover:bg-surface-2",
        )}
      >
        <FileCode2 className={cn("size-3 shrink-0", active ? "text-violet-soft" : "text-subtle")} />
        <span className="truncate">{node.name.split("/").pop()}</span>
      </button>
    );
  }
  return (
    <div>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{ paddingLeft: depth * 12 + 8 }}
        className="flex w-full items-center gap-1 py-[3px] text-left font-mono text-[11.5px] text-foreground hover:bg-surface-2"
      >
        {open ? (
          <ChevronDown className="size-3 text-subtle" />
        ) : (
          <ChevronRight className="size-3 text-subtle" />
        )}
        <Folder className="size-3 text-info/80" />
        <span className="truncate">{node.name}</span>
      </button>
      {open
        ? node.children?.map((c) => (
            <Node
              key={c.name}
              node={c}
              depth={depth + 1}
              selectedFileId={selectedFileId}
              onSelect={onSelect}
            />
          ))
        : null}
    </div>
  );
}

function Codebase() {
  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const fileTree = useFileTree(activeRepositoryId);
  const search = Route.useSearch();

  if (repositories.isLoading) {
    return <CenterMessage>Loading…</CenterMessage>;
  }
  if (activeRepositoryId === null) {
    return (
      <CenterMessage>
        No repository connected yet.{" "}
        <Link to="/onboarding" className="focus-ring text-violet-soft hover:underline">
          Connect a repository
        </Link>
      </CenterMessage>
    );
  }
  if (fileTree.isLoading) {
    return <CenterMessage>Loading file tree…</CenterMessage>;
  }
  if (fileTree.isError || !fileTree.data) {
    return <CenterMessage tone="danger">Failed to load the file tree.</CenterMessage>;
  }
  if (!fileTree.data.analyzed) {
    return (
      <CenterMessage>
        This repository hasn't been analyzed yet.{" "}
        <Link to="/onboarding" className="focus-ring text-violet-soft hover:underline">
          Run the first analysis
        </Link>
      </CenterMessage>
    );
  }

  return (
    <CodebaseExplorer
      repositoryId={activeRepositoryId}
      files={fileTree.data.files}
      searchFile={search.file ?? null}
      highlightStart={search.line_start ?? null}
      highlightEnd={search.line_end ?? null}
    />
  );
}

function CodebaseExplorer({
  repositoryId,
  files,
  searchFile,
  highlightStart,
  highlightEnd,
}: {
  repositoryId: string;
  files: FileTreeEntry[];
  searchFile: string | null;
  highlightStart: number | null;
  highlightEnd: number | null;
}) {
  const navigate = useNavigate();
  const [filter, setFilter] = useState("");

  // The URL is the source of truth for which file is open — not local state
  // — so a citation-chip deep link (or any other producer of this URL shape
  // in the future) just works without a second code path.
  const selectedFileId = useMemo(
    () => (searchFile ? (files.find((f) => f.path === searchFile)?.id ?? null) : null),
    [files, searchFile],
  );

  const fileContent = useFileContent(repositoryId, selectedFileId);
  const fileInsight = useFileInsight(repositoryId, selectedFileId);

  const selectedEntry = selectedFileId
    ? (files.find((f) => f.id === selectedFileId) ?? null)
    : null;

  const effectiveHighlightEnd = highlightEnd ?? highlightStart;

  const selectFile = (fileId: string) => {
    const entry = files.find((f) => f.id === fileId);
    if (!entry) return;
    void navigate({ to: "/app/codebase", search: { file: entry.path }, replace: true });
  };

  // Scroll the referenced line into view once its file's content has
  // actually loaded — not on every render, only when the loaded content or
  // the target line itself changes.
  useEffect(() => {
    if (highlightStart == null) return;
    if (!fileContent.data || fileContent.data.binary || fileContent.data.truncated) return;
    const el = document.getElementById(`line-${highlightStart}`);
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [fileContent.data, highlightStart]);

  const filteredFiles = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? files.filter((f) => f.path.toLowerCase().includes(q)) : files;
  }, [files, filter]);
  const tree = useMemo(() => buildFileTree(filteredFiles), [filteredFiles]);
  const moduleCount = useMemo(() => new Set(files.map((f) => fileModule(f.path))).size, [files]);

  const languageLabel = selectedEntry?.language
    ? (LANGUAGE_LABELS[selectedEntry.language] ?? selectedEntry.language)
    : null;
  const complexity = fileInsight.data?.complexity;
  const complexityTone = complexity?.label
    ? severityTone(complexity.label.toLowerCase())
    : "neutral";

  return (
    <div className="grid h-[calc(100vh-3rem)] grid-cols-[224px_1fr] gap-0 xl:grid-cols-[224px_1fr_320px]">
      {/* LEFT — file tree */}
      <aside className="flex min-h-0 flex-col border-r border-border bg-surface/40">
        <div className="flex items-center gap-2 border-b border-border px-2.5 py-2">
          <Search className="size-3.5 text-subtle" />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Find file"
            className="w-full bg-transparent font-mono text-[11.5px] outline-none placeholder:text-subtle"
          />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto py-1.5">
          {tree.length ? (
            tree.map((n) => (
              <Node
                key={n.name}
                node={n}
                depth={0}
                selectedFileId={selectedFileId}
                onSelect={selectFile}
              />
            ))
          ) : (
            <p className="p-3 text-center text-[11.5px] text-subtle">No files match "{filter}"</p>
          )}
        </div>
        <div className="border-t border-border px-2.5 py-2 font-mono text-[10.5px] text-subtle">
          {files.length.toLocaleString()} files · {moduleCount.toLocaleString()} modules
        </div>
      </aside>

      {/* CENTER — code viewer. `min-w-0` is load-bearing: without it, a
          CSS Grid item's automatic minimum width defaults to its content's
          intrinsic (max-content) width — a real long source line
          (`whitespace-pre`, never wraps) can be far wider than this
          column's `1fr` track, which silently blows the whole grid out and
          pushes the right-hand AI Insights column off-screen. The mock's
          own short `authServiceSource` fixture never hit this; real
          HireSight file content does. */}
      <section className="flex min-h-0 min-w-0 flex-col bg-background">
        {selectedEntry ? (
          <div className="flex min-w-0 items-center gap-2 border-b border-border px-3 py-2">
            <FileCode2 className="size-3.5 shrink-0 text-violet-soft" />
            <span className="min-w-0 truncate font-mono text-[12px]">{selectedEntry.path}</span>
            {languageLabel ? (
              <Tag tone="neutral" mono className="shrink-0">
                {languageLabel}
              </Tag>
            ) : null}
            {complexity?.score != null ? (
              <Tag tone={complexityTone} className="shrink-0">
                complexity {complexity.score} ({complexity.label})
              </Tag>
            ) : null}
            <span className="ml-auto shrink-0 font-mono text-[11px] text-subtle">
              {selectedEntry.loc.toLocaleString()} lines
            </span>
          </div>
        ) : null}
        <div className="min-h-0 flex-1 overflow-auto">
          {!selectedEntry ? (
            <p className="p-8 text-center text-[13px] text-subtle">
              Select a file from the tree to view its contents.
            </p>
          ) : fileContent.isLoading ? (
            <p className="p-8 text-center text-[13px] text-subtle">Loading file…</p>
          ) : fileContent.isError || !fileContent.data ? (
            <p className="p-8 text-center text-[13px] text-danger">
              Failed to load this file's content.
            </p>
          ) : fileContent.data.binary ? (
            <p className="p-8 text-center text-[13px] text-subtle">
              This is a binary file — not previewable here.
            </p>
          ) : fileContent.data.truncated ? (
            <p className="p-8 text-center text-[13px] text-subtle">
              This file is too large to preview inline (over GitHub's ~1MB content limit).
            </p>
          ) : (
            <pre className="min-w-full">
              <code className="block py-2 font-mono text-[12px] leading-[1.55]">
                {(fileContent.data.content ?? "").split("\n").map((l, i) => {
                  const lineNumber = i + 1;
                  const isReferenced =
                    highlightStart != null &&
                    lineNumber >= highlightStart &&
                    lineNumber <= (effectiveHighlightEnd ?? highlightStart);
                  return (
                    <span
                      key={i}
                      id={`line-${lineNumber}`}
                      className={cn(
                        "flex px-1 hover:bg-surface/70",
                        l.includes("NOTE:") && "bg-warning/[0.07]",
                        isReferenced && "bg-violet/[0.16] hover:bg-violet/[0.2]",
                      )}
                    >
                      <span className="w-10 shrink-0 pr-3 text-right text-subtle select-none">
                        {lineNumber}
                      </span>
                      <span className="whitespace-pre text-secondary-foreground">
                        {highlight(l)}
                      </span>
                    </span>
                  );
                })}
              </code>
            </pre>
          )}
        </div>
      </section>

      {/* RIGHT — AI insights. `min-w-0` for the same reason as the code
          viewer above — a long real dependency/API path here is otherwise
          just as capable of blowing out this column's fixed 320px track. */}
      <aside className="hidden min-h-0 min-w-0 flex-col overflow-y-auto border-l border-border bg-surface/40 xl:flex">
        <PanelHeader
          title="AI Insights"
          subtitle={selectedEntry?.path.split("/").pop() ?? ""}
          icon={<Sparkles className="size-3.5" />}
        />
        {!selectedFileId ? (
          <p className="p-3.5 text-center text-[12px] text-subtle">
            Select a file to see its insights.
          </p>
        ) : fileInsight.isLoading ? (
          <p className="p-3.5 text-center text-[12px] text-subtle">Loading insight…</p>
        ) : fileInsight.isError || !fileInsight.data ? (
          <p className="p-3.5 text-center text-[12px] text-danger">
            Failed to load this file's insight.
          </p>
        ) : (
          <div className="space-y-4 p-3.5">
            <div>
              <p className="label-xs mb-1.5">Purpose</p>
              {fileInsight.data.purpose ? (
                <p className="text-[12.5px] leading-relaxed text-secondary-foreground">
                  {fileInsight.data.purpose}
                </p>
              ) : (
                <p className="text-[12px] text-subtle italic">
                  No AI-generated purpose available for this file.
                </p>
              )}
            </div>

            <div>
              <p className="label-xs mb-1.5">Complexity</p>
              {fileInsight.data.complexity.score != null ? (
                <div className="rounded-md border border-border bg-surface p-3">
                  <div className="flex items-baseline justify-between">
                    <span className="font-mono text-[17px]">
                      {fileInsight.data.complexity.score}
                    </span>
                    <Tag tone={complexityTone}>{fileInsight.data.complexity.label}</Tag>
                  </div>
                  <div className="mt-2">
                    <ScoreBar
                      value={Math.min(100, (fileInsight.data.complexity.score / 30) * 100)}
                      tone={complexityTone}
                    />
                  </div>
                  <p className="mt-2 font-mono text-[11px] text-subtle">
                    {fileInsight.data.complexity.functions} function
                    {fileInsight.data.complexity.functions === 1 ? "" : "s"}
                  </p>
                </div>
              ) : (
                <p className="text-[12px] text-subtle">No functions in this file.</p>
              )}
            </div>

            <div>
              <p className="label-xs mb-1.5">Dependencies</p>
              {fileInsight.data.dependencies.length ? (
                <ul className="space-y-1">
                  {fileInsight.data.dependencies.map((d) => (
                    <li key={d} className="font-mono text-[11.5px] break-all text-muted-foreground">
                      {d}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[11.5px] text-subtle">none</p>
              )}
            </div>

            <div>
              <p className="label-xs mb-1.5">Used by</p>
              {fileInsight.data.used_by.length ? (
                <ul className="space-y-1">
                  {fileInsight.data.used_by.map((d) => (
                    <li key={d} className="font-mono text-[11.5px] break-all text-muted-foreground">
                      {d}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[11.5px] text-subtle">none</p>
              )}
            </div>

            <div>
              <p className="label-xs mb-1.5">Related APIs</p>
              {fileInsight.data.apis.length ? (
                <ul className="space-y-1">
                  {fileInsight.data.apis.map((a) => (
                    <li key={a} className="font-mono text-[11.5px] break-all text-cyan">
                      {a}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[11.5px] text-subtle">none</p>
              )}
            </div>

            {fileInsight.data.explanation ? (
              <AiNote title="AI explanation">{fileInsight.data.explanation}</AiNote>
            ) : (
              <p className="rounded-md border border-dashed border-border bg-surface-2/30 p-3 text-center text-[11.5px] text-subtle">
                No AI-generated explanation available for this file.
              </p>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}
