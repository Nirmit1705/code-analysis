import { createFileRoute, Link } from "@tanstack/react-router";
import { formatDistanceToNow } from "date-fns";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowUp,
  ArrowUpRight,
  BotMessageSquare,
  Boxes,
  CircleAlert,
  Copy,
  CornerDownRight,
  CornerUpLeft,
  Database,
  FileCheck2,
  Files,
  GitBranch,
  MessageSquareDashed,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  TriangleAlert,
  Webhook,
} from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import { useActiveRepositoryId } from "@/lib/active-repo";
import {
  useSendAssistantMessage,
  useSuggestedQuestions,
  type Reference,
} from "@/lib/queries/assistant";
import { completedRuns, useAnalysisRuns } from "@/lib/queries/history";
import { useRepositories } from "@/lib/queries/repos";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/assistant")({
  head: () => ({
    meta: [
      { title: "AI Codebase Assistant — CodeLens AI" },
      {
        name: "description",
        content:
          "Ask questions about your repository and get answers grounded in real files, with every citation linked to source.",
      },
      { property: "og:title", content: "AI Codebase Assistant — CodeLens AI" },
      {
        property: "og:description",
        content: "Repository-aware answers with file citations, not a generic chatbot.",
      },
    ],
  }),
  component: Assistant,
});

/** One question and (eventually) its answer. The backend is stateless, so
 * turns live only in this page's state — there is no saved history to show,
 * which is why the old mock "Conversations" / "Context in use" panels are gone. */
interface Turn {
  id: string;
  question: string;
  status: "pending" | "done" | "error";
  answer: string;
  references: Reference[];
  followUps: string[];
  /** The backend's own `insufficient_context` flag: nothing in the index was
   * relevant enough to ground an answer (so `references` is `[]`). */
  limited: boolean;
}

// ---------------------------------------------------------------------------
// Answer text: a small local formatter for the markdown the model actually
// uses (inline code, **bold**, *italic*, `-` lists) — no markdown dependency.
// ---------------------------------------------------------------------------

/** Handles inline code spans, **bold**, and *italic*. `**bold**` is matched
 * before `*italic*` so a bold span's own asterisks are never read as italic. */
function renderInlineMarkdown(line: string): ReactNode[] {
  const parts = line.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 1) {
      return (
        <code
          key={i}
          className="rounded-md border border-border bg-surface-3 px-1 py-px font-mono text-[0.88em] whitespace-nowrap text-violet-soft"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("**") && part.endsWith("**") && part.length > 3) {
      return (
        <strong key={i} className="font-semibold text-foreground">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 1) {
      return <em key={i}>{part.slice(1, -1)}</em>;
    }
    return part;
  });
}

/** The model's text sometimes contains a literal two-character `\n` where it
 * means a line break (a known quirk of models writing multi-paragraph text
 * inside one JSON string). Normalized once, before any line-based parsing. */
function normalizeEscapedWhitespace(text: string): string {
  return (
    text
      .replace(/\\r\\n/g, "\n")
      .replace(/\\n/g, "\n")
      .replace(/\\t/g, "\t")
      // The model also emits narrow / no-break / thin spaces (U+202F, U+00A0,
      // U+2009) where it means a normal space; in this font they read as
      // "inserver" and stop lines wrapping, so they become plain spaces.
      .replace(/[   ]/g, " ")
  );
}

/** `- `/`* `-prefixed lines (a space is required after the marker, so an
 * opening `*italic*` span never matches) become a real list; everything else
 * is a paragraph. Blank lines are dropped — block spacing comes from CSS. */
function renderAnswerBody(text: string): ReactNode[] {
  const lines = normalizeEscapedWhitespace(text)
    .split("\n")
    .filter((line) => line.trim().length > 0);
  const blocks: ReactNode[] = [];
  let currentList: string[] = [];
  let ordered = false;

  const flushList = () => {
    if (currentList.length === 0) return;
    const List = ordered ? "ol" : "ul";
    blocks.push(
      <List
        key={blocks.length}
        className={cn(
          "flex flex-col gap-2 pl-5 marker:text-violet",
          ordered ? "list-decimal" : "list-disc",
          blocks.length > 0 && "mt-3",
        )}
      >
        {currentList.map((item, i) => (
          <li key={i}>{renderInlineMarkdown(item)}</li>
        ))}
      </List>,
    );
    currentList = [];
  };

  for (const line of lines) {
    // `- item` / `* item` (bullets) and `1. item` / `1) item` (numbered steps,
    // which the model uses for walkthroughs) each become a real list.
    const bulletMatch = /^[-*]\s+(.+)/.exec(line.trim());
    const numberMatch = /^\d+[.)]\s+(.+)/.exec(line.trim());
    const match = bulletMatch ?? numberMatch;
    if (match) {
      const isNumbered = bulletMatch === null;
      if (currentList.length > 0 && ordered !== isNumbered) flushList();
      ordered = isNumbered;
      currentList.push(match[1]!);
      continue;
    }
    flushList();
    blocks.push(
      <p key={blocks.length} className={blocks.length > 0 ? "mt-3" : undefined}>
        {renderInlineMarkdown(line)}
      </p>,
    );
  }
  flushList();

  return blocks;
}

// ---------------------------------------------------------------------------
// Sources
// ---------------------------------------------------------------------------

function splitPath(path: string): { dir: string; file: string } {
  const i = path.lastIndexOf("/");
  return i === -1
    ? { dir: "", file: path }
    : { dir: path.slice(0, i + 1), file: path.slice(i + 1) };
}

function extensionOf(path: string): string {
  const file = splitPath(path).file;
  const dot = file.lastIndexOf(".");
  return dot > 0 ? file.slice(dot + 1, dot + 5) : "file";
}

function lineRange(ref: Reference): string {
  return ref.line_start === ref.line_end
    ? `L${ref.line_start}`
    : `L${ref.line_start}–${ref.line_end}`;
}

/** Every field comes straight off the backend's `Reference`, never parsed out
 * of the answer's text. Opens the Codebase screen at the exact lines. */
function SourceCard({ reference }: { reference: Reference }) {
  const { dir, file } = splitPath(reference.file_path);
  return (
    <Link
      to="/app/codebase"
      search={{
        file: reference.file_path,
        line_start: reference.line_start,
        line_end: reference.line_end,
      }}
      className="focus-ring group relative grid grid-cols-[34px_minmax(0,1fr)] items-center gap-x-3 gap-y-1 rounded-xl border border-border bg-surface py-2.5 pr-9 pl-2.5 transition-colors hover:border-violet/45 hover:bg-surface-2"
    >
      <span className="row-span-2 grid size-[34px] place-items-center rounded-[9px] border border-border bg-surface-3 font-mono text-[11px] font-semibold text-info uppercase">
        {extensionOf(reference.file_path)}
      </span>
      {/* `direction: rtl` + <bdi> keeps the file name visible and truncates the
          start of a long directory path instead of the end. */}
      <span
        className="truncate text-left font-mono text-[12px] leading-snug text-subtle"
        style={{ direction: "rtl" }}
        title={reference.file_path}
      >
        <bdi style={{ direction: "ltr", unicodeBidi: "isolate" }}>
          {dir}
          <b className="font-semibold text-foreground">{file}</b>
        </bdi>
      </span>
      <span className="flex min-w-0 items-center gap-2 font-mono text-[11.5px] text-muted-foreground">
        {reference.symbol_name ? (
          <span className="truncate text-violet-soft">{reference.symbol_name}</span>
        ) : null}
        <span className="ml-auto shrink-0 text-subtle tabular-nums">{lineRange(reference)}</span>
      </span>
      <ArrowUpRight className="absolute top-3 right-3 size-4 text-subtle opacity-0 transition-opacity group-hover:text-violet-soft group-hover:opacity-100" />
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Suggested questions
// ---------------------------------------------------------------------------

/** The backend's suggestions are deterministic templates
 * (`assistant_service.list_suggested_questions`), so the kind can be read off
 * the text: a module question or an endpoint question. Anything else is left
 * unlabelled rather than guessed at. */
function describeSuggestion(q: string): {
  kind: "Module" | "Endpoint" | "Question";
  body: ReactNode;
} {
  const code = (text: string) => (
    <code key={text} className="font-mono text-[0.92em] text-violet-soft">
      {text}
    </code>
  );
  const module = /^How does the (.+) module work\?$/.exec(q);
  if (module) return { kind: "Module", body: <>How does the {code(module[1]!)} module work?</> };
  const endpoint = /^What does ((?:GET|POST|PUT|PATCH|DELETE|ALL) \S+) do\?$/.exec(q);
  if (endpoint) return { kind: "Endpoint", body: <>What does {code(endpoint[1]!)} do?</> };
  return { kind: "Question", body: q };
}

function SuggestionCard({
  question,
  disabled,
  onAsk,
}: {
  question: string;
  disabled: boolean;
  onAsk: (q: string) => void;
}) {
  const { kind, body } = describeSuggestion(question);
  const Icon = kind === "Endpoint" ? Webhook : Boxes;
  return (
    <button
      type="button"
      onClick={() => onAsk(question)}
      disabled={disabled}
      className="focus-ring group relative flex flex-col items-start gap-2 rounded-[14px] border border-border bg-surface py-3.5 pr-10 pl-4 text-left transition-all hover:-translate-y-px hover:border-violet/45 hover:bg-surface-2 disabled:opacity-50"
    >
      <span className="flex items-center gap-1.5 text-[10.5px] font-semibold tracking-[0.08em] text-violet-soft uppercase">
        <Icon className="size-3.5" />
        {kind}
      </span>
      <span className="text-[13px] leading-snug font-medium break-words text-foreground">
        {body}
      </span>
      <ArrowUpRight className="absolute top-3.5 right-3.5 size-4 text-subtle group-hover:text-violet-soft" />
    </button>
  );
}

// ---------------------------------------------------------------------------
// Composer
// ---------------------------------------------------------------------------

function Composer({
  value,
  onChange,
  onSubmit,
  disabled,
  pending,
  large,
  scopeNote,
  placeholder,
  textareaRef,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  disabled: boolean;
  pending: boolean;
  large?: boolean;
  scopeNote: string;
  placeholder: string;
  textareaRef?: React.Ref<HTMLTextAreaElement>;
}) {
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      className="rounded-[20px] border border-border-strong bg-surface shadow-[0_24px_50px_-30px_rgba(0,0,0,0.95),inset_0_1px_0_oklch(1_0_0/0.04)] transition-[border-color,box-shadow] focus-within:border-violet/60 focus-within:shadow-[0_0_0_4px_oklch(0.703_0.198_42/0.12),0_24px_50px_-30px_rgba(0,0,0,0.95)]"
    >
      <label htmlFor={large ? "assistant-question-hero" : "assistant-question"} className="sr-only">
        Your question
      </label>
      <textarea
        id={large ? "assistant-question-hero" : "assistant-question"}
        ref={textareaRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            onSubmit();
          }
        }}
        rows={large ? 2 : 1}
        disabled={disabled}
        placeholder={placeholder}
        className={cn(
          "block w-full resize-none bg-transparent px-5 pt-4 pb-1.5 text-[13px] leading-relaxed text-foreground outline-none placeholder:text-subtle disabled:cursor-not-allowed",
          large ? "min-h-[66px] pt-[18px]" : "max-h-40 min-h-[54px]",
        )}
      />
      <div className="flex items-center gap-3 py-2 pr-3 pl-5">
        <span className="flex items-center gap-1.5 text-[11.5px] text-subtle">
          <ShieldCheck className="size-3.5 text-success" />
          {scopeNote}
        </span>
        <span className="ml-auto hidden text-[11.5px] text-subtle sm:block">
          <kbd className="rounded-[5px] border border-border bg-surface-2 px-1.5 font-mono text-[11px] text-muted-foreground">
            Enter
          </kbd>{" "}
          to send
          {large ? " · " : ""}
          {large ? (
            <>
              <kbd className="rounded-[5px] border border-border bg-surface-2 px-1.5 font-mono text-[11px] text-muted-foreground">
                Shift
              </kbd>{" "}
              +{" "}
              <kbd className="rounded-[5px] border border-border bg-surface-2 px-1.5 font-mono text-[11px] text-muted-foreground">
                Enter
              </kbd>{" "}
              for a new line
            </>
          ) : null}
        </span>
        <button
          type="submit"
          disabled={disabled || !value.trim() || pending}
          aria-label="Send"
          className="focus-ring grid size-[38px] shrink-0 place-items-center rounded-xl border-0 bg-primary text-primary-foreground shadow-[0_0_26px_-8px_var(--primary)] hover:bg-[oklch(0.745_0.19_44)] disabled:cursor-not-allowed disabled:opacity-50 max-sm:ml-auto"
        >
          <ArrowUp className="size-[18px]" />
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// One question + answer
// ---------------------------------------------------------------------------

function TurnBlock({
  turn,
  focused,
  dimmed,
  busy,
  suggestions,
  onFocus,
  onAsk,
  onRetry,
  onEdit,
}: {
  turn: Turn;
  focused: boolean;
  dimmed: boolean;
  busy: boolean;
  suggestions: string[];
  onFocus: () => void;
  onAsk: (q: string) => void;
  onRetry: () => void;
  onEdit: () => void;
}) {
  const failed = turn.status === "error";
  const limited = turn.status === "done" && turn.limited;
  const count = turn.references.length;

  return (
    <article
      id={`turn-${turn.id}`}
      className="scroll-mt-4 border-b border-border py-6 first:pt-2 last:border-b-0"
    >
      <div className="flex items-start gap-3">
        <span className="mt-px grid size-7 shrink-0 place-items-center rounded-lg bg-violet/20 font-mono text-[11px] font-semibold text-violet-soft">
          MO
        </span>
        <h2 className="pt-0.5 text-[17px] leading-snug font-semibold tracking-[-0.02em] text-balance">
          {renderInlineMarkdown(turn.question)}
        </h2>
      </div>

      {/* The selected answer sits on a lifted surface; with more than one
          answer on the page, the others dim back (and return on hover). */}
      <div
        className={cn(
          "mt-4 ml-10 rounded-[18px] border transition-[opacity,background-color,box-shadow] duration-150",
          failed
            ? "border-danger/35 bg-surface"
            : limited
              ? "border-warning/30 bg-surface"
              : "border-border bg-surface",
          focused &&
            !failed &&
            !limited &&
            "bg-surface-2 shadow-[0_22px_44px_-28px_rgba(0,0,0,0.95),inset_0_1px_0_oklch(1_0_0/0.07)]",
          dimmed && "opacity-55 hover:opacity-90",
        )}
      >
        <div className="flex flex-wrap items-center gap-2.5 border-b border-border py-3 pr-4 pl-[18px]">
          <span className="flex items-center gap-2 text-[12.5px] font-semibold">
            <span
              className={cn(
                "grid size-[22px] place-items-center rounded-md border",
                failed
                  ? "border-danger/40 bg-danger/12 text-danger"
                  : "border-violet/35 bg-violet/14 text-violet-soft",
              )}
            >
              {failed ? (
                <TriangleAlert className="size-3.5" />
              ) : (
                <BotMessageSquare className="size-3.5" />
              )}
            </span>
            CodeLens AI
          </span>

          {turn.status === "pending" ? (
            <span className="flex items-center gap-2 text-[12px] text-muted-foreground">
              <span className="size-[7px] animate-pulse rounded-full bg-primary" />
              Reading the code and writing an answer
            </span>
          ) : null}

          {limited ? (
            <span className="flex items-center gap-1.5 rounded-full bg-warning/13 px-2.5 py-0.5 text-[11.5px] font-semibold text-warning">
              <CircleAlert className="size-3.5" />
              Limited context
            </span>
          ) : null}
          {failed ? (
            <span className="rounded-full bg-danger/14 px-2.5 py-0.5 text-[11.5px] font-semibold text-danger">
              Couldn&apos;t answer
            </span>
          ) : null}

          {turn.status === "done" && !limited && count > 0 ? (
            <button
              type="button"
              onClick={onFocus}
              title="Show these sources in the side panel"
              className={cn(
                "focus-ring hidden items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold transition-colors xl:flex",
                focused
                  ? "bg-primary text-primary-foreground"
                  : "bg-success/12 text-success hover:bg-success/22",
              )}
            >
              <FileCheck2 className="size-3.5" />
              {focused ? "Sources shown" : `${count} ${count === 1 ? "source" : "sources"}`}
            </button>
          ) : null}
          {turn.status === "done" && !limited && count > 0 ? (
            <span className="flex items-center gap-1.5 rounded-full bg-success/12 px-2.5 py-0.5 text-[11.5px] font-semibold text-success xl:hidden">
              <FileCheck2 className="size-3.5" />
              {count} {count === 1 ? "source" : "sources"}
            </span>
          ) : null}

          {turn.status === "done" ? (
            <div className="ml-auto flex gap-0.5">
              <button
                type="button"
                onClick={() => {
                  void navigator.clipboard
                    .writeText(turn.answer)
                    .then(() => toast.success("Answer copied"))
                    .catch(() => toast.error("Couldn't copy the answer"));
                }}
                className="focus-ring flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] text-subtle hover:bg-surface-2 hover:text-foreground"
              >
                <Copy className="size-3.5" />
                Copy
              </button>
              <button
                type="button"
                onClick={() => onAsk(turn.question)}
                disabled={busy}
                className="focus-ring flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] text-subtle hover:bg-surface-2 hover:text-foreground disabled:opacity-50"
              >
                <RotateCcw className="size-3.5" />
                Ask again
              </button>
            </div>
          ) : null}
        </div>

        {turn.status === "pending" ? (
          <div className="flex flex-col gap-2.5 px-[22px] pt-5 pb-6" aria-hidden>
            {["w-[92%]", "w-[78%]", "w-[86%]", "w-[54%]"].map((w) => (
              <span key={w} className={cn("h-3 animate-pulse rounded-md bg-surface-3", w)} />
            ))}
          </div>
        ) : (
          // Neutral tracking and a touch of extra word spacing: this font's
          // default tracking runs plain prose tight.
          <div className="max-w-[68ch] px-[22px] pt-[18px] pb-5 text-[13px] leading-[1.72] tracking-normal text-secondary-foreground [word-spacing:0.05em]">
            {renderAnswerBody(turn.answer)}
          </div>
        )}

        {failed ? (
          <div className="flex flex-wrap gap-2 px-[22px] pb-5">
            <button
              type="button"
              onClick={onRetry}
              disabled={busy}
              className="focus-ring flex items-center gap-2 rounded-[10px] border border-primary bg-primary px-3.5 py-2 text-[12.5px] font-semibold text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] disabled:opacity-50"
            >
              <RefreshCw className="size-3.5" />
              Try again
            </button>
            <button
              type="button"
              onClick={onEdit}
              className="focus-ring rounded-[10px] border border-border-strong bg-surface-2 px-3.5 py-2 text-[12.5px] font-semibold hover:border-violet/50"
            >
              Edit question
            </button>
          </div>
        ) : null}

        {limited && suggestions.length > 0 ? (
          <div className="mx-[22px] mb-5 flex flex-col gap-1.5 rounded-xl border border-dashed border-border-strong p-3 text-[12.5px] text-muted-foreground">
            <span className="label-xs">Questions the index can answer</span>
            {suggestions.slice(0, 2).map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => onAsk(q)}
                disabled={busy}
                className="focus-ring flex items-center gap-2 rounded-md text-left hover:text-foreground disabled:opacity-50"
              >
                <CornerDownRight className="size-3.5 shrink-0 text-subtle" />
                {describeSuggestion(q).body}
              </button>
            ))}
          </div>
        ) : null}

        {turn.status === "done" && turn.followUps.length > 0 ? (
          <div className="px-3.5 pb-3.5">
            <p className="label-xs px-2 pt-2.5 pb-1.5">Keep going</p>
            {turn.followUps.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => onAsk(q)}
                disabled={busy}
                className="focus-ring group flex w-full items-center gap-2.5 rounded-[10px] px-3 py-2.5 text-left text-[12.5px] text-muted-foreground transition-colors hover:bg-surface-3 hover:text-foreground disabled:opacity-50"
              >
                <CornerDownRight className="size-3.5 shrink-0 text-subtle group-hover:text-violet-soft" />
                {renderInlineMarkdown(q)}
              </button>
            ))}
          </div>
        ) : null}

        {/* Below the xl breakpoint there is no side panel, so each answer keeps
            its own collapsible list of sources. */}
        {turn.status === "done" && count > 0 ? (
          <details className="border-t border-border px-[22px] py-3 xl:hidden">
            <summary className="cursor-pointer text-[12.5px] font-medium text-muted-foreground">
              {count} {count === 1 ? "source" : "sources"}
            </summary>
            <div className="mt-3 flex flex-col gap-2">
              {turn.references.map((ref, i) => (
                <SourceCard key={`${ref.file_path}-${ref.line_start}-${i}`} reference={ref} />
              ))}
            </div>
          </details>
        ) : null}
      </div>
    </article>
  );
}

// ---------------------------------------------------------------------------
// Right rail
// ---------------------------------------------------------------------------

function RailSection({
  title,
  count,
  children,
}: {
  title: string;
  count?: number;
  children: ReactNode;
}) {
  return (
    <section>
      <div className="mb-2.5 flex items-baseline justify-between">
        <h3 className="label-xs">{title}</h3>
        {count !== undefined ? (
          <span className="font-mono text-[12px] text-subtle tabular-nums">{count}</span>
        ) : null}
      </div>
      {children}
    </section>
  );
}

function Assistant() {
  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const activeRepository = repositories.data?.find((r) => r.id === activeRepositoryId) ?? null;
  const runsQuery = useAnalysisRuns(activeRepositoryId);
  const latestRun = completedRuns(runsQuery.data)[0] ?? null;
  const suggested = useSuggestedQuestions(activeRepositoryId);
  const sendMessage = useSendAssistantMessage(activeRepositoryId ?? "");

  const [turns, setTurns] = useState<Turn[]>([]);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const heroInput = useRef<HTMLTextAreaElement>(null);
  const dockInput = useRef<HTMLTextAreaElement>(null);
  const counter = useRef(0);

  const questions = suggested.data?.questions ?? [];
  const latestTurn = turns[turns.length - 1] ?? null;
  const focusedTurn = turns.find((t) => t.id === focusId) ?? latestTurn;
  const busy = sendMessage.isPending;
  const needsAnalysis = !runsQuery.isLoading && !runsQuery.isError && latestRun === null;

  // A new question brings its answer into view.
  useEffect(() => {
    if (!latestTurn) return;
    document.getElementById(`turn-${latestTurn.id}`)?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  }, [turns.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const patchTurn = (id: string, patch: Partial<Turn>) =>
    setTurns((all) => all.map((t) => (t.id === id ? { ...t, ...patch } : t)));

  /** `replaceId` re-runs a failed question in place instead of appending. */
  const ask = async (question: string, replaceId?: string) => {
    const trimmed = question.trim();
    if (!trimmed || !activeRepositoryId || busy) return;

    const id = replaceId ?? `t${++counter.current}`;
    const pending: Turn = {
      id,
      question: trimmed,
      status: "pending",
      answer: "",
      references: [],
      followUps: [],
      limited: false,
    };
    setTurns((all) =>
      replaceId ? all.map((t) => (t.id === id ? pending : t)) : [...all, pending],
    );
    setFocusId(id);
    setInput("");

    // `mutateAsync` + try/catch, not `.mutate(vars, {onSuccess})` — the
    // per-call callbacks silently never fired in this codebase.
    try {
      const result = await sendMessage.mutateAsync(trimmed);
      patchTurn(id, {
        status: "done",
        answer: result.answer,
        references: result.references,
        followUps: result.follow_up_questions,
        limited: result.insufficient_context,
      });
    } catch (err) {
      patchTurn(id, {
        status: "error",
        answer:
          err instanceof ApiError
            ? err.message
            : "Something went wrong answering that. Try again in a moment.",
      });
    }
  };

  const editQuestion = (question: string) => {
    setInput(question);
    (turns.length === 0 ? heroInput : dockInput).current?.focus();
  };

  const focusTurn = (id: string, scroll: boolean) => {
    setFocusId(id);
    if (scroll) {
      document
        .getElementById(`turn-${id}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };

  const repoName = activeRepository?.name ?? "this repository";
  const isStart = turns.length === 0;
  const sourcesOfFocus = focusedTurn?.status === "done" ? focusedTurn.references : [];

  return (
    <div className="grid h-[calc(100vh-3rem)] grid-cols-1 xl:grid-cols-[minmax(0,1fr)_336px]">
      <section className="flex min-h-0 flex-col">
        <div className="flex flex-wrap items-center gap-3 px-10 pt-3.5 pb-3 max-md:px-4">
          <h1 className="text-[15px] font-semibold tracking-[-0.02em]">AI Assistant</h1>
          {latestRun ? (
            <span className="flex items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 py-0.5 font-mono text-[11.5px] text-muted-foreground tabular-nums">
              <GitBranch className="size-3.5 text-violet-soft" />
              {latestRun.branch} · {latestRun.commit_sha.slice(0, 7)}
            </span>
          ) : null}
          {!isStart ? (
            <button
              type="button"
              onClick={() => {
                setTurns([]);
                setFocusId(null);
                setInput("");
              }}
              className="focus-ring ml-auto flex items-center gap-1.5 rounded-[9px] border border-border px-3 py-1.5 text-[12px] text-muted-foreground hover:border-border-strong hover:bg-surface hover:text-foreground"
            >
              <Plus className="size-3.5" />
              New question
            </button>
          ) : null}
        </div>

        <div
          className={cn(
            "min-h-0 flex-1 overflow-y-auto px-10 pt-2 pb-7 max-md:px-4",
            isStart && "flex items-center",
          )}
        >
          <div className="mx-auto w-full max-w-[780px]">
            {!activeRepositoryId ? (
              <div className="p-10 text-center">
                <p className="text-[13px] text-subtle">No repository connected yet.</p>
                <Link
                  to="/onboarding"
                  className="focus-ring mt-2 inline-block text-[12.5px] text-violet-soft hover:underline"
                >
                  Connect a repository
                </Link>
              </div>
            ) : needsAnalysis ? (
              <div className="p-10 text-center">
                <p className="text-[13px] text-subtle">
                  {repoName} hasn&apos;t been analyzed yet, so there is nothing to answer from.
                </p>
                <Link
                  to="/onboarding"
                  className="focus-ring mt-2 inline-block text-[12.5px] text-violet-soft hover:underline"
                >
                  Run the first analysis
                </Link>
              </div>
            ) : isStart ? (
              <div>
                <h2 className="text-[40px] leading-[1.12] font-extrabold tracking-[-0.035em] text-balance max-md:text-[30px]">
                  Ask anything about{" "}
                  <code className="rounded-[10px] bg-violet/12 px-[0.2em] font-mono text-[0.86em] font-semibold tracking-[-0.03em] text-violet-soft">
                    {repoName}
                  </code>
                </h2>
                <p className="mt-3.5 max-w-[56ch] text-[14px] leading-relaxed text-muted-foreground">
                  Answers are built from the code in your latest analysis, and each one lists the
                  files it came from.
                </p>
                <div className="mt-6">
                  <Composer
                    large
                    value={input}
                    onChange={setInput}
                    onSubmit={() => void ask(input)}
                    disabled={!activeRepositoryId}
                    pending={busy}
                    scopeNote="Read-only, from the indexed code"
                    placeholder="Ask about a module, endpoint, symbol or workflow…"
                    textareaRef={heroInput}
                  />
                </div>
                {questions.length > 0 ? (
                  <>
                    <p className="label-xs mt-7">Start with one of these</p>
                    <div className="mt-3 grid gap-2.5 sm:grid-cols-2">
                      {questions.map((q) => (
                        <SuggestionCard
                          key={q}
                          question={q}
                          disabled={busy}
                          onAsk={(v) => void ask(v)}
                        />
                      ))}
                    </div>
                  </>
                ) : null}
              </div>
            ) : (
              turns.map((turn) => (
                <TurnBlock
                  key={turn.id}
                  turn={turn}
                  focused={focusedTurn?.id === turn.id}
                  dimmed={turns.length > 1 && focusedTurn?.id !== turn.id}
                  busy={busy}
                  suggestions={questions}
                  onFocus={() => focusTurn(turn.id, false)}
                  onAsk={(q) => void ask(q)}
                  onRetry={() => void ask(turn.question, turn.id)}
                  onEdit={() => editQuestion(turn.question)}
                />
              ))
            )}
          </div>
        </div>

        {!isStart && activeRepositoryId && !needsAnalysis ? (
          <div className="px-10 pt-1.5 pb-5 max-md:px-4">
            <div className="mx-auto max-w-[780px]">
              <Composer
                value={input}
                onChange={setInput}
                onSubmit={() => void ask(input)}
                disabled={!activeRepositoryId}
                pending={busy}
                scopeNote="Each question is answered on its own"
                placeholder="Ask a follow-up, or a new question…"
                textareaRef={dockInput}
              />
            </div>
          </div>
        ) : null}
      </section>

      <aside
        aria-label="Sources and context"
        className="hidden min-h-0 flex-col gap-5 overflow-y-auto border-l border-border bg-surface/40 p-4 xl:flex"
      >
        <section className="rounded-[14px] border border-border bg-surface px-3.5 pt-3.5 pb-3">
          <p className="label-xs mb-2.5">Answers come from</p>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3.5 gap-y-1.5 text-[12px]">
            {(
              [
                ["Repository", activeRepository?.name ?? "—"],
                ["Branch", latestRun?.branch ?? "—"],
                ["Commit", latestRun ? latestRun.commit_sha.slice(0, 7) : "—"],
                [
                  "Indexed",
                  latestRun ? `${latestRun.file_count} files · ${latestRun.api_count} APIs` : "—",
                ],
                [
                  "Analyzed",
                  latestRun?.completed_at
                    ? formatDistanceToNow(new Date(latestRun.completed_at), { addSuffix: true })
                    : "—",
                ],
              ] as const
            ).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-subtle">{k}</dt>
                <dd className="text-right font-mono text-[11.5px] tabular-nums">{v}</dd>
              </div>
            ))}
          </dl>
        </section>

        {isStart ? (
          <RailSection title="How answers work">
            <ul className="flex flex-col gap-3.5">
              {[
                {
                  icon: Database,
                  title: "Built from your code",
                  body: "Only files in the latest analysis are used. Nothing is pulled from the web.",
                },
                {
                  icon: Files,
                  title: "Every answer shows its sources",
                  body: "Open any file at the exact lines it used.",
                },
                {
                  icon: MessageSquareDashed,
                  title: "One question at a time",
                  body: "Earlier messages are not remembered, so include the detail you need in each question.",
                },
              ].map((item) => (
                <li key={item.title} className="grid grid-cols-[30px_1fr] items-start gap-3">
                  <span className="grid size-[30px] place-items-center rounded-[9px] bg-violet/12 text-violet-soft">
                    <item.icon className="size-[15px]" />
                  </span>
                  <span className="text-[12px] leading-relaxed text-muted-foreground">
                    <b className="mb-px block text-[12.5px] font-semibold text-foreground">
                      {item.title}
                    </b>
                    {item.body}
                  </span>
                </li>
              ))}
            </ul>
          </RailSection>
        ) : (
          <>
            <RailSection title="Sources" count={sourcesOfFocus.length}>
              {focusedTurn ? (
                <p className="mb-3 text-[12px] leading-snug font-semibold text-foreground">
                  For “{focusedTurn.question}”
                </p>
              ) : null}
              {focusedTurn && latestTurn && focusedTurn.id !== latestTurn.id ? (
                <button
                  type="button"
                  onClick={() => focusTurn(latestTurn.id, true)}
                  className="focus-ring mb-3 flex items-center gap-1.5 rounded-lg border border-border-strong bg-surface-2 px-2.5 py-1 text-[11.5px] font-semibold text-violet-soft hover:border-violet/50"
                >
                  <CornerUpLeft className="size-3.5" />
                  Back to the latest answer
                </button>
              ) : null}

              {sourcesOfFocus.length > 0 ? (
                <div className="flex flex-col gap-2">
                  {sourcesOfFocus.map((ref, i) => (
                    <SourceCard key={`${ref.file_path}-${ref.line_start}-${i}`} reference={ref} />
                  ))}
                  <p className="mt-1.5 px-0.5 text-[11.5px] leading-relaxed text-subtle">
                    Each file opens in Codebase at the lines the answer used. This panel follows
                    your newest answer. To look at an earlier one, select its sources label, or pick
                    its question below.
                  </p>
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-border-strong px-3.5 py-4 text-[12px] leading-relaxed text-muted-foreground">
                  <b className="mb-1 block text-foreground">
                    {focusedTurn?.status === "pending"
                      ? "Appear with the answer"
                      : focusedTurn?.status === "error"
                        ? "No answer, so no sources"
                        : "Nothing relevant was indexed"}
                  </b>
                  {focusedTurn?.status === "pending"
                    ? "The files used will be listed here as soon as the answer arrives."
                    : focusedTurn?.status === "error"
                      ? "Sources are shown only for answers the assistant completed."
                      : "No file matched this question closely enough to cite. Try naming a module, endpoint or file."}
                </div>
              )}
            </RailSection>

            <RailSection title="This session" count={turns.length}>
              <div className="flex flex-col gap-0.5">
                {turns.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => focusTurn(t.id, true)}
                    className={cn(
                      "focus-ring flex w-full items-start gap-2.5 rounded-[10px] px-2.5 py-2 text-left text-[12.5px] leading-snug transition-colors hover:bg-surface-2 hover:text-foreground",
                      focusedTurn?.id === t.id
                        ? "bg-surface-2 text-foreground"
                        : "text-muted-foreground",
                    )}
                  >
                    <span
                      className={cn(
                        "mt-1.5 size-[7px] shrink-0 rounded-full",
                        focusedTurn?.id === t.id
                          ? "bg-primary shadow-[0_0_0_3px_oklch(0.703_0.198_42/0.2)]"
                          : "bg-border-strong",
                      )}
                    />
                    <span className="min-w-0 flex-1">{t.question}</span>
                    {t.status === "done" && t.references.length > 0 ? (
                      <span className="shrink-0 pl-2 font-mono text-[11.5px] text-subtle tabular-nums">
                        {t.references.length}
                      </span>
                    ) : null}
                  </button>
                ))}
              </div>
            </RailSection>
          </>
        )}
      </aside>
    </div>
  );
}
