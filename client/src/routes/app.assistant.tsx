import { createFileRoute, Link } from "@tanstack/react-router";
import { useState, type ReactNode } from "react";
import { Sparkles, ArrowUp, FileCode2, Plus, History } from "lucide-react";

import { Panel, PanelHeader, Tag } from "@/components/ui-kit";
import { ApiError } from "@/lib/api";
import { useActiveRepositoryId } from "@/lib/active-repo";
import {
  useSendAssistantMessage,
  useSuggestedQuestions,
  type Reference,
} from "@/lib/queries/assistant";
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

type Msg =
  | { role: "user"; text: string }
  | {
      role: "ai";
      text: string;
      scope?: string;
      refs?: Reference[];
      followUps?: string[];
      error?: boolean;
    };

/** `file_path` first, `symbol_name` (when the chunk resolved to one) and
 * the real line range after — every field comes straight off the backend's
 * `Reference`, never parsed out of the answer's own text (design decision #4). */
function referenceLabel(ref: Reference): string {
  const location = ref.symbol_name ? `${ref.file_path} · ${ref.symbol_name}` : ref.file_path;
  return `${location}:${ref.line_start}-${ref.line_end}`;
}

/** Handles inline code spans, **bold**, and *italic* — the markdown
 * constructs the model's answers actually use — without a markdown-parsing
 * dependency (design decision #10 stays intact; this is a small local
 * formatter, not a parser). Anything else (lists, links, headings) still
 * renders as plain text, same as before. `code` reuses the exact `mono
 * text-violet-soft` styling the old mock's own inline-code spans used.
 * `**bold**` is matched before `*italic*` in the alternation so a bold
 * span's own asterisks are never mistaken for an italic one. */
function renderInlineMarkdown(line: string): ReactNode[] {
  const parts = line.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 1) {
      return (
        <code key={i} className="mono text-violet-soft">
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("**") && part.endsWith("**") && part.length > 3) {
      return (
        <strong key={i} className="font-semibold">
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

/** The model's own answer text sometimes contains a literal two-character
 * `\n` (backslash + "n") where it means an actual line break — observed
 * live, not hypothetical: Groq's strict-mode JSON output is a real newline
 * once `json.loads` decodes it server-side, but the model occasionally
 * writes a literal escape sequence as part of the *content* of that string
 * (a known quirk of models asked to produce structured multi-paragraph text
 * inside one JSON field), which survives JSON parsing on both ends as the
 * literal two characters, not a real newline — `text.split("\n")` below
 * can't split on those, so they rendered as bare `\n`/`\t` characters in the
 * UI. Normalized here, once, before any line-based parsing. */
function normalizeEscapedWhitespace(text: string): string {
  return text.replace(/\\r\\n/g, "\n").replace(/\\n/g, "\n").replace(/\\t/g, "\t");
}

/** `- `/`* `-prefixed lines (a required space after the marker, so this
 * never fires on an opening `*italic*` span, which has no space after its
 * `*`) are grouped into a real `<ul>` instead of rendering as bare
 * characters with no indentation. Everything else stays a plain `<p>` with
 * light inline formatting (see `renderInlineMarkdown`). Blank lines (from
 * the model's own `\n\n` paragraph breaks) are dropped; the gap comes from
 * spacing between blocks instead of rendering an empty line. */
function renderAnswerBody(text: string): ReactNode[] {
  const lines = normalizeEscapedWhitespace(text)
    .split("\n")
    .filter((line) => line.trim().length > 0);
  const blocks: ReactNode[] = [];
  let currentList: string[] = [];

  const flushList = () => {
    if (currentList.length === 0) return;
    blocks.push(
      <ul
        key={blocks.length}
        className={cn("list-disc space-y-1 pl-5", blocks.length > 0 && "mt-2")}
      >
        {currentList.map((item, i) => (
          <li key={i}>{renderInlineMarkdown(item)}</li>
        ))}
      </ul>,
    );
    currentList = [];
  };

  for (const line of lines) {
    const bulletMatch = /^[-*]\s+(.+)/.exec(line.trim());
    if (bulletMatch) {
      currentList.push(bulletMatch[1]);
      continue;
    }
    flushList();
    blocks.push(
      <p key={blocks.length} className={blocks.length > 0 ? "mt-2" : undefined}>
        {renderInlineMarkdown(line)}
      </p>,
    );
  }
  flushList();

  return blocks;
}

function AnswerText({ text }: { text: string }) {
  return <>{renderAnswerBody(text)}</>;
}

function Assistant() {
  const activeRepositoryId = useActiveRepositoryId();
  const repositories = useRepositories();
  const activeRepository = repositories.data?.find((r) => r.id === activeRepositoryId) ?? null;
  const suggested = useSuggestedQuestions(activeRepositoryId);
  const sendMessage = useSendAssistantMessage(activeRepositoryId ?? "");

  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");

  const ask = async (question: string) => {
    const trimmed = question.trim();
    if (!trimmed || !activeRepositoryId || sendMessage.isPending) return;

    setMessages((m) => [...m, { role: "user", text: trimmed }]);
    setInput("");

    try {
      const result = await sendMessage.mutateAsync(trimmed);
      setMessages((m) => [
        ...m,
        {
          role: "ai",
          text: result.answer,
          scope: `${result.references.length} reference${result.references.length === 1 ? "" : "s"}`,
          refs: result.references,
          followUps: result.follow_up_questions,
        },
      ]);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.message
          : "Something went wrong answering that — please try again.";
      setMessages((m) => [...m, { role: "ai", text: message, error: true }]);
    }
  };

  return (
    <div className="grid h-[calc(100vh-3rem)] grid-cols-1 xl:grid-cols-[1fr_288px]">
      <section className="flex min-h-0 flex-col">
        <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
          <Sparkles className="size-3.5 text-violet-soft" />
          <h1 className="text-[13px] font-semibold">AI Codebase Assistant</h1>
          {activeRepository ? (
            <Tag tone="violet" mono>
              {activeRepository.full_name} · {activeRepository.default_branch}
            </Tag>
          ) : null}
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-5">
          <div className="mx-auto max-w-3xl space-y-4">
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
            ) : messages.length === 0 ? (
              <div className="p-10 text-center">
                <p className="text-[13px] text-subtle">
                  Ask anything about this repository — a module, an endpoint, a symbol, or how
                  something works.
                </p>
              </div>
            ) : null}

            {messages.map((m, i) =>
              m.role === "user" ? (
                <div
                  key={i}
                  className="ml-auto w-fit max-w-[80%] rounded-lg rounded-br-sm border border-border bg-surface-2 px-3.5 py-2.5 text-[13px]"
                >
                  {m.text}
                </div>
              ) : (
                <div
                  key={i}
                  className={cn(
                    "rounded-lg border p-4",
                    m.error
                      ? "border-danger/25 bg-danger/[0.06]"
                      : "border-violet/20 bg-violet/[0.055]",
                  )}
                >
                  <p
                    className={cn(
                      "mb-2 flex items-center gap-1.5 text-[11px] tracking-wide uppercase",
                      m.error ? "text-danger" : "text-violet-soft",
                    )}
                  >
                    <span
                      className={cn(
                        "size-1.5 rounded-full",
                        m.error ? "bg-danger" : "bg-violet pulse-node",
                      )}
                    />
                    CodeLens AI
                    {m.scope ? (
                      <span className="ml-1 font-mono text-[10.5px] normal-case text-subtle">
                        {m.scope}
                      </span>
                    ) : null}
                  </p>
                  <div className="text-[13px] leading-relaxed text-secondary-foreground">
                    <AnswerText text={m.text} />
                  </div>
                  {m.refs && m.refs.length > 0 ? (
                    <div className="mt-3 border-t border-violet/15 pt-3">
                      <p className="label-xs mb-1.5">Referenced files</p>
                      <div className="flex flex-wrap gap-1.5">
                        {m.refs.map((ref, refIndex) => (
                          <Link
                            key={`${ref.file_path}-${ref.line_start}-${refIndex}`}
                            to="/app/codebase"
                            search={{
                              file: ref.file_path,
                              line_start: ref.line_start,
                              line_end: ref.line_end,
                            }}
                            className="focus-ring flex items-center gap-1.5 rounded-sm border border-border bg-surface px-2 py-1 font-mono text-[11px] text-muted-foreground transition-colors hover:border-violet/40 hover:text-foreground"
                          >
                            <FileCode2 className="size-3 text-violet-soft" /> {referenceLabel(ref)}
                          </Link>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  {m.followUps && m.followUps.length > 0 ? (
                    <div className="mt-3 flex flex-wrap gap-1.5 border-t border-violet/15 pt-3">
                      {m.followUps.map((q) => (
                        <button
                          key={q}
                          onClick={() => ask(q)}
                          disabled={sendMessage.isPending}
                          className="focus-ring rounded-full border border-violet/25 bg-surface px-2.5 py-1 text-[11.5px] text-violet-soft transition-colors hover:border-violet/45 hover:bg-violet/10 disabled:opacity-50"
                        >
                          {q}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              ),
            )}

            {sendMessage.isPending ? (
              <div className="rounded-lg border border-violet/20 bg-violet/[0.055] p-4">
                <p className="flex items-center gap-1.5 text-[11px] tracking-wide text-violet-soft uppercase">
                  <span className="size-1.5 rounded-full bg-violet pulse-node" /> CodeLens AI
                </p>
                <p className="mt-2 text-[13px] text-subtle">Thinking…</p>
              </div>
            ) : null}
          </div>
        </div>

        <div className="border-t border-border p-3">
          <div className="mx-auto max-w-3xl">
            {suggested.data && suggested.data.questions.length > 0 ? (
              <div className="mb-2 flex flex-wrap gap-1.5">
                {suggested.data.questions.map((q) => (
                  <button
                    key={q}
                    onClick={() => ask(q)}
                    disabled={sendMessage.isPending}
                    className="focus-ring rounded-full border border-border bg-surface px-2.5 py-1 text-[11.5px] text-muted-foreground transition-colors hover:border-violet/40 hover:text-foreground disabled:opacity-50"
                  >
                    {q}
                  </button>
                ))}
              </div>
            ) : null}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                ask(input);
              }}
              className={cn(
                "flex items-end gap-2 rounded-lg border border-border bg-surface p-2 focus-within:border-violet/45",
              )}
            >
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                rows={2}
                disabled={!activeRepositoryId}
                placeholder={
                  activeRepositoryId
                    ? "Ask about a module, endpoint, symbol or workflow…"
                    : "Connect a repository to ask questions…"
                }
                className="max-h-32 min-h-9 w-full resize-none bg-transparent px-1.5 py-1 text-[13px] outline-none placeholder:text-subtle disabled:cursor-not-allowed"
              />
              <button
                type="submit"
                disabled={!activeRepositoryId || !input.trim() || sendMessage.isPending}
                className="focus-ring grid size-8 shrink-0 place-items-center rounded-md border border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] hover:bg-[oklch(0.745_0.19_44)] disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="Send"
              >
                <ArrowUp className="size-3.5" />
              </button>
            </form>
          </div>
        </div>
      </section>

      <aside className="hidden min-h-0 flex-col gap-3 overflow-y-auto border-l border-border bg-surface/40 p-3 xl:flex">
        <Panel>
          <PanelHeader title="Conversations" actions={<Plus className="size-3.5 text-subtle" />} />
          <ul className="divide-y divide-border">
            {[
              ["Authentication flow", "active"],
              ["Redis usage audit", "2h ago"],
              ["Why is scheduler slow?", "yesterday"],
              ["Payment webhook idempotency", "3d ago"],
            ].map(([t, s]) => (
              <li key={t} className="px-3 py-2">
                <p className="truncate text-[12px]">{t}</p>
                <p className="font-mono text-[10.5px] text-subtle">{s}</p>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel>
          <PanelHeader title="Context in use" icon={<History className="size-3.5" />} />
          <ul className="space-y-1.5 p-3">
            {[
              "services/auth/**",
              "apps/gateway/src/routes/**",
              "packages/cache/src/redis.ts",
              "db/schema.prisma",
            ].map((c) => (
              <li
                key={c}
                className="truncate rounded-sm border border-border bg-surface-2/50 px-2 py-1 font-mono text-[11px] text-muted-foreground"
              >
                {c}
              </li>
            ))}
          </ul>
        </Panel>
      </aside>
    </div>
  );
}
