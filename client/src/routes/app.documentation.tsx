import { createFileRoute, Link } from "@tanstack/react-router";

import { cn } from "@/lib/utils";

export const Route = createFileRoute("/app/documentation")({
  head: () => ({
    meta: [
      { title: "Documentation — CodeLens AI" },
      { name: "description", content: "Documentation written from your codebase is coming soon." },
      { property: "og:title", content: "Documentation — CodeLens AI" },
      { property: "og:description", content: "Documentation written from your codebase is coming soon." },
    ],
  }),
  component: Documentation,
});

// An illustration of the idea, not real output: a few source lines and the
// documentation line each one would produce. The doc text is derived only from
// the code shown beside it.
const rows = [
  {
    file: "package.json",
    code: [`"main": "src/server.ts",`, `"scripts": { "dev": "tsx watch" }`],
    heading: "Overview",
    line: "Starts from src/server.ts",
    bars: [86, 58],
  },
  {
    file: "src/api/users.ts",
    code: [`import { auth } from "../auth";`, `import { db } from "../data";`],
    heading: "Architecture",
    line: "The API layer depends on auth and data",
    bars: [74, 46],
  },
  {
    file: "src/auth/service.ts",
    code: [`export class AuthService {`, `  signIn(email, password) {`],
    heading: "Modules",
    line: "AuthService signs users in with an email and password",
    bars: [80, 38],
  },
  {
    file: "src/api/routes.ts",
    code: [`router.post("/login", signIn);`, `router.get("/users/:id", getUser);`],
    heading: "API reference",
    line: "POST /login and GET /users/:id",
    bars: [68, 52],
  },
];

function Documentation() {
  return (
    <div className="mx-auto flex min-h-[calc(100dvh-6rem)] max-w-[1280px] items-center p-4">
      <div className="grid w-full items-center gap-10 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-14">
        <div>
          <p className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
            <span className="size-1.5 rounded-full bg-violet pulse-node" />
            In development
          </p>
          <h1 className="mt-4 text-[clamp(2rem,3.4vw,3.1rem)] leading-[1.06] font-semibold tracking-[-0.03em] text-balance">
            Documentation that stays true to your code
          </h1>
          <p className="mt-5 max-w-[46ch] text-[14px] leading-relaxed text-muted-foreground">
            CodeLens will write an overview, an architecture guide, module notes and an API reference from your analyzed
            repositories, each one tied to the source it came from.
          </p>

          <div className="mt-8 flex flex-wrap gap-2.5">
            <Link
              to="/app/architecture"
              className="focus-ring rounded-md bg-violet px-3.5 py-2 text-[12.5px] font-semibold text-background transition-colors hover:bg-violet-soft"
            >
              Open Architecture Explorer
            </Link>
            <Link
              to="/app/api"
              className="focus-ring rounded-md border border-border-strong px-3.5 py-2 text-[12.5px] font-medium text-foreground transition-colors hover:bg-surface-2"
            >
              Open API Reference
            </Link>
          </div>
          <p className="mt-3 text-[11.5px] text-subtle">Both are available now.</p>
        </div>

        <figure className="min-w-0">
          <div className="panel overflow-hidden">
            <div className="hidden grid-cols-[minmax(0,1fr)_56px_minmax(0,1fr)] border-b border-border px-5 py-2.5 text-[11.5px] text-subtle md:grid">
              <span>Your code</span>
              <span />
              <span>What CodeLens writes</span>
            </div>
            <ol>
              {rows.map((r, i) => (
                <li
                  key={r.heading}
                  style={{ "--d": `${0.5 + i * 0.85}s` } as React.CSSProperties}
                  className={cn(
                    "grid items-center gap-3 px-5 py-4 md:grid-cols-[minmax(0,1fr)_56px_minmax(0,1fr)] md:gap-0",
                    i > 0 && "border-t border-border",
                  )}
                >
                  <div className="seq-lit min-w-0 rounded-md border border-border border-l-violet/60 bg-surface-2/50 px-3 py-2.5">
                    <p className="truncate font-mono text-[10.5px] text-subtle">{r.file}</p>
                    <pre className="mt-1.5 overflow-hidden font-mono text-[11px] leading-[1.6] text-secondary-foreground">
                      {r.code.join("\n")}
                    </pre>
                  </div>

                  <div aria-hidden className="hidden items-center md:flex">
                    <span className="seq-grow h-px flex-1 origin-left bg-violet/60" />
                    <span className="seq-fade size-1.5 shrink-0 rounded-full bg-violet" />
                  </div>

                  <div className="min-w-0 md:pl-3">
                    <h3 className="seq-fade text-[13px] font-semibold">{r.heading}</h3>
                    <p className="seq-fade mt-0.5 text-[12px] leading-snug text-muted-foreground">{r.line}</p>
                    <div aria-hidden className="mt-2.5 space-y-1.5">
                      {r.bars.map((w, b) => (
                        <span
                          key={b}
                          style={{ width: `${w}%`, animationDelay: `calc(var(--d) + ${0.55 + b * 0.12}s)` }}
                          className="seq-grow block h-1.5 origin-left rounded-full bg-surface-3"
                        />
                      ))}
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          </div>
          <figcaption className="mt-3 text-[11.5px] text-subtle">
            Illustration. Sample code and the documentation lines it would produce.
          </figcaption>
        </figure>
      </div>
    </div>
  );
}
