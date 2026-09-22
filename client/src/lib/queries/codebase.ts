/** TanStack Query hooks for `server/app/api/v1/codebase.py` (Phase 6.7,
 * already built and signed off) — wired into `app.codebase.tsx` (Phase 6.8).
 * Response shapes mirror `server/app/schemas/codebase.py` field-for-field,
 * kept snake_case (no camelCase translation), matching this codebase's
 * actual established convention (`queries/dashboard.ts`, `queries/architecture.ts`). */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

/** One file from the latest completed run's flat file list — see
 * `buildFileTree` below for turning this into the nested shape the tree
 * renderer expects. */
export interface FileTreeEntry {
  id: string;
  path: string;
  language: string | null;
  size_bytes: number;
  loc: number;
}

/** Mirrors `FileTreeRead`. `analyzed: false` is the full "not analyzed yet"
 * response, matching `DashboardRead`/`ArchitectureRead`'s own convention. */
export interface FileTreeRead {
  analyzed: boolean;
  analysis_run_id: string | null;
  files: FileTreeEntry[];
}

/** `content` is `null` whenever `truncated` or `binary` is `true` — a file
 * over GitHub's ~1MB inline-content cap, or bytes that aren't valid UTF-8. */
export interface FileContentRead {
  path: string;
  content: string | null;
  truncated: boolean;
  binary: boolean;
}

export interface ComplexitySummary {
  score: number | null;
  label: string | null;
  loc: number;
  functions: number;
}

/** Mirrors `FileInsightRead`. `purpose`/`explanation` are real, lazily
 * generated AI content as of Phase 8.7 (`file_insight_service.py`) — `null`
 * only when the file has no content to ground on or generation failed,
 * never a fabricated guess. No `branches` field: there's no real per-file
 * decision-node count anywhere in Phase 3/4's persisted data (only each
 * function's already-aggregated cyclomatic complexity is kept), so this UI
 * simply doesn't show one rather than inventing a number the way the old
 * mock's `fileInsight.complexity.branches` did. */
export interface FileInsightRead {
  path: string;
  purpose: string | null;
  complexity: ComplexitySummary;
  dependencies: string[];
  used_by: string[];
  apis: string[];
  explanation: string | null;
}

/** `GET /repos/{id}/files` — the latest completed run's files, flat. `null`
 * while the active repository hasn't resolved yet. */
export function useFileTree(repositoryId: string | null) {
  return useQuery({
    queryKey: ["file-tree", repositoryId],
    queryFn: () => apiFetch<FileTreeRead>(`/repos/${repositoryId}/files`),
    enabled: repositoryId !== null,
    retry: false,
  });
}

/** `GET /repos/{id}/files/{file_id}` — live content read straight from
 * GitHub at the analyzed commit. Both `repositoryId` and `fileId` must be
 * resolved before this fires. */
export function useFileContent(repositoryId: string | null, fileId: string | null) {
  return useQuery({
    queryKey: ["file-content", repositoryId, fileId],
    queryFn: () => apiFetch<FileContentRead>(`/repos/${repositoryId}/files/${fileId}`),
    enabled: repositoryId !== null && fileId !== null,
    retry: false,
  });
}

/** `GET /repos/{id}/files/{file_id}/insight` — complexity/dependencies/
 * usedBy/apis from Phase 4 rows. */
export function useFileInsight(repositoryId: string | null, fileId: string | null) {
  return useQuery({
    queryKey: ["file-insight", repositoryId, fileId],
    queryFn: () => apiFetch<FileInsightRead>(`/repos/${repositoryId}/files/${fileId}/insight`),
    enabled: repositoryId !== null && fileId !== null,
    retry: false,
  });
}

/** One node in the nested tree the existing renderer (`app.codebase.tsx`)
 * expects: a directory has `children`, a file has `file: true` and a real
 * `fileId` to select/fetch by. */
export interface CodebaseTreeNode {
  name: string;
  children?: CodebaseTreeNode[];
  file?: boolean;
  fileId?: string;
}

interface RawDir {
  name: string;
  dirs: Map<string, RawDir>;
  files: Map<string, FileTreeEntry>;
}

function newDir(name: string): RawDir {
  return { name, dirs: new Map(), files: new Map() };
}

/** Turns the backend's flat `files[]` into the nested shape the tree
 * renderer already expects — the plan's own "no renderer changes needed"
 * design (`docs/PHASE_6_PLAN.md` 6.8). A chain of directories that each
 * hold nothing but one lone subdirectory is compacted into a single node
 * (e.g. a real `server/src/controllers` chain renders as one row, not
 * three) — the same "compact folders" convention the original mock's own
 * `fileTree` export already assumed (its `"web"` node contained a leaf
 * literally named `"src/main.tsx"`); this function is what actually
 * produces that shape from real data instead of it being hand-written. */
export function buildFileTree(files: FileTreeEntry[]): CodebaseTreeNode[] {
  const root = newDir("");
  for (const file of files) {
    const segments = file.path.split("/");
    let dir = root;
    for (let i = 0; i < segments.length - 1; i++) {
      const segment = segments[i]!;
      let next = dir.dirs.get(segment);
      if (!next) {
        next = newDir(segment);
        dir.dirs.set(segment, next);
      }
      dir = next;
    }
    dir.files.set(segments[segments.length - 1]!, file);
  }
  return convertDir(root);
}

function convertDir(dir: RawDir): CodebaseTreeNode[] {
  const dirNodes = [...dir.dirs.values()]
    .sort((a, b) => a.name.localeCompare(b.name))
    .map(compactDir);
  const fileNodes = [...dir.files.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, file]): CodebaseTreeNode => ({ name, file: true, fileId: file.id }));
  return [...dirNodes, ...fileNodes];
}

function compactDir(dir: RawDir): CodebaseTreeNode {
  const names = [dir.name];
  let cur = dir;
  while (cur.files.size === 0 && cur.dirs.size === 1) {
    const only = [...cur.dirs.values()][0]!;
    names.push(only.name);
    cur = only;
  }
  return { name: names.join("/"), children: convertDir(cur) };
}

/** The real "containing directory" for a file path, mirroring the
 * backend's own `File.module` semantic exactly (root-level files get
 * `"."`) — used to compute a real, non-fabricated module count for the
 * tree panel's footer stat straight from the already-fetched flat file
 * list, without the backend needing to expose a separate field for it. */
export function fileModule(path: string): string {
  const lastSlash = path.lastIndexOf("/");
  return lastSlash === -1 ? "." : path.slice(0, lastSlash);
}
