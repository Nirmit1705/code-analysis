import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { Sparkles, ArrowUp, FileCode2, Plus, History } from "lucide-react";

import { Panel, PanelHeader, Tag } from "@/components/ui-kit";
import { suggestedQuestions, repo } from "@/lib/repo-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/assistant")({
  head: () => ({
    meta: [
      { title: "AI Codebase Assistant — Substrate" },
      { name: "description", content: "Ask questions about your repository and get answers grounded in real files, with every citation linked to source." },
      { property: "og:title", content: "AI Codebase Assistant — Substrate" },
      { property: "og:description", content: "Repository-aware answers with file citations, not a generic chatbot." },
    ],
  }),
  component: Assistant,
});

type Msg = { role: "user" | "ai"; text: React.ReactNode; refs?: string[]; scope?: string };

const seeded: Msg[] = [
  { role: "user", text: "How does authentication work?" },
  {
    role: "ai",
    scope: "9 files read · auth module",
    text: (
      <>
        Authentication begins in <code className="mono text-violet-soft">auth.controller.ts</code>, which validates the
        request DTO and delegates to <code className="mono text-violet-soft">AuthService</code>. The service verifies
        credentials with bcrypt, then mints a 15-minute access token and a refresh token; the refresh token is stored in
        Redis under <code className="mono text-violet-soft">session:&lt;token&gt;</code>.
        <br />
        <br />
        On every subsequent request the gateway forwards the bearer token to{" "}
        <code className="mono text-violet-soft">jwt.strategy.ts</code>, which verifies the signature and resolves the
        organization claim. <code className="mono text-violet-soft">auth.guard.ts</code> then enforces route scopes such
        as <code className="mono text-cyan">org:admin</code>.
        <br />
        <br />
        Two things worth knowing: the Redis session TTL (24h) outlives the access token, so a revoked session can still
        resolve until expiry; and three non-identity services import AuthService directly rather than consuming the
        published guard.
      </>
    ),
    refs: ["auth.controller.ts", "auth.service.ts", "jwt.strategy.ts", "auth.guard.ts", "redis.ts"],
  },
];

const canned: Record<string, Msg> = {
  "Trace the payment flow.": {
    role: "ai",
    scope: "6 files read · billing module",
    text: (
      <>
        Checkout starts on <code className="mono text-violet-soft">settings.billing.tsx</code> and posts to{" "}
        <code className="mono text-cyan">POST /api/billing/checkout</code>, guarded by{" "}
        <code className="mono text-violet-soft">auth.guard.ts</code> with the <code className="mono text-cyan">org:admin</code>{" "}
        scope. <code className="mono text-violet-soft">BillingService.createCheckoutSession()</code> resolves the Stripe
        customer, applies proration and returns a hosted checkout URL. Fulfilment happens asynchronously in{" "}
        <code className="mono text-violet-soft">webhooks/stripe.ts</code>, which is signature-verified but not idempotent —
        duplicate deliveries can double-count seats.
      </>
    ),
    refs: ["settings.billing.tsx", "billing.controller.ts", "billing.service.ts", "stripe.ts", "webhooks/stripe.ts"],
  },
  "Where is Redis used?": {
    role: "ai",
    scope: "12 files read · 4 modules",
    text: (
      <>
        Redis is accessed only through <code className="mono text-violet-soft">packages/cache/src/redis.ts</code> and is
        used for three concerns: refresh-token sessions (Auth Service), rate-limit counters (API Gateway) and BullMQ
        transcript queues (Interview Service). <code className="mono text-violet-soft">scheduler.ts</code> creates a
        connection per request instead of using the shared pool, which is the main source of latency above 200 rps.
      </>
    ),
    refs: ["redis.ts", "queues.ts", "auth.service.ts", "rate-limit.ts", "scheduler.ts"],
  },
  "Which modules depend on UserService?": {
    role: "ai",
    scope: "graph query · 1,912 edges",
    text: (
      <>
        Seven modules import <code className="mono text-violet-soft">UserService</code>: auth, interview, billing,
        notification, analytics-gateway, admin-console and the seeding CLI. Of those, notification and analytics only use{" "}
        <code className="mono text-violet-soft">findById()</code> — candidates for a narrower read interface.
      </>
    ),
    refs: ["user.service.ts", "user.repository.ts", "auth.service.ts", "notification.service.ts"],
  },
  "What happens when a user registers?": {
    role: "ai",
    scope: "8 files read · trace built",
    text: (
      <>
        The registration form posts to <code className="mono text-cyan">POST /api/register</code> (rate limited 5/60s).
        AuthController maps the DTO, AuthService hashes the password at bcrypt cost 12 and{" "}
        <code className="mono text-violet-soft">UserRepository.insert()</code> writes users and org_members in one
        transaction. A database trigger emits an audit_log row the analytics worker later aggregates. Open the Feature
        Tracer for the full path.
      </>
    ),
    refs: ["register.tsx", "auth.controller.ts", "auth.service.ts", "user.repository.ts", "schema.prisma"],
  },
};

function Assistant() {
  const [messages, setMessages] = useState<Msg[]>(seeded);
  const [input, setInput] = useState("");

  const ask = (q: string) => {
    const answer =
      canned[q] ??
      ({
        role: "ai",
        scope: "18,402 chunks searched",
        text: (
          <>
            I searched the indexed repository for <span className="text-foreground">“{q}”</span>. The closest relevant
            modules are the Auth Service and API Gateway. Open the referenced files to continue, or narrow the question to
            a module, endpoint or symbol name.
          </>
        ),
        refs: ["auth.service.ts", "server.ts", "rate-limit.ts"],
      } as Msg);
    setMessages((m) => [...m, { role: "user", text: q }, answer]);
    setInput("");
  };

  return (
    <div className="grid h-[calc(100vh-3rem)] grid-cols-1 xl:grid-cols-[1fr_288px]">
      <section className="flex min-h-0 flex-col">
        <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
          <Sparkles className="size-3.5 text-violet-soft" />
          <h1 className="text-[13px] font-semibold">AI Codebase Assistant</h1>
          <Tag tone="violet" mono>{repo.short} · main</Tag>
          <span className="ml-auto font-mono text-[11px] text-subtle">context: 3,184 files · 18,402 chunks</span>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-5">
          <div className="mx-auto max-w-3xl space-y-4">
            {messages.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="ml-auto w-fit max-w-[80%] rounded-lg rounded-br-sm border border-border bg-surface-2 px-3.5 py-2.5 text-[13px]">
                  {m.text}
                </div>
              ) : (
                <div key={i} className="rounded-lg border border-violet/20 bg-violet/[0.055] p-4">
                  <p className="mb-2 flex items-center gap-1.5 text-[11px] tracking-wide text-violet-soft uppercase">
                    <span className="size-1.5 rounded-full bg-violet pulse-node" /> Substrate
                    {m.scope ? <span className="ml-1 font-mono text-[10.5px] normal-case text-subtle">{m.scope}</span> : null}
                  </p>
                  <div className="text-[13px] leading-relaxed text-secondary-foreground">{m.text}</div>
                  {m.refs ? (
                    <div className="mt-3 border-t border-violet/15 pt-3">
                      <p className="label-xs mb-1.5">Referenced files</p>
                      <div className="flex flex-wrap gap-1.5">
                        {m.refs.map((f) => (
                          <Link
                            key={f}
                            to="/app/codebase"
                            className="focus-ring flex items-center gap-1.5 rounded-sm border border-border bg-surface px-2 py-1 font-mono text-[11px] text-muted-foreground transition-colors hover:border-violet/40 hover:text-foreground"
                          >
                            <FileCode2 className="size-3 text-violet-soft" /> {f}
                          </Link>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              ),
            )}
          </div>
        </div>

        <div className="border-t border-border p-3">
          <div className="mx-auto max-w-3xl">
            <div className="mb-2 flex flex-wrap gap-1.5">
              {suggestedQuestions.map((q) => (
                <button
                  key={q}
                  onClick={() => ask(q)}
                  className="focus-ring rounded-full border border-border bg-surface px-2.5 py-1 text-[11.5px] text-muted-foreground transition-colors hover:border-violet/40 hover:text-foreground"
                >
                  {q}
                </button>
              ))}
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (input.trim()) ask(input.trim());
              }}
              className={cn(
                "flex items-end gap-2 rounded-lg border border-border bg-surface p-2 focus-within:border-violet/45",
              )}
            >
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                rows={2}
                placeholder="Ask about a module, endpoint, symbol or workflow…"
                className="max-h-32 min-h-9 w-full resize-none bg-transparent px-1.5 py-1 text-[13px] outline-none placeholder:text-subtle"
              />
              <button
                type="submit"
                className="focus-ring grid size-8 shrink-0 place-items-center rounded-md border border-primary bg-primary text-primary-foreground shadow-[0_0_24px_-8px_var(--primary)] hover:bg-[oklch(0.745_0.19_44)]"
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
            {["services/auth/**", "apps/gateway/src/routes/**", "packages/cache/src/redis.ts", "db/schema.prisma"].map((c) => (
              <li key={c} className="truncate rounded-sm border border-border bg-surface-2/50 px-2 py-1 font-mono text-[11px] text-muted-foreground">
                {c}
              </li>
            ))}
          </ul>
        </Panel>
      </aside>
    </div>
  );
}
