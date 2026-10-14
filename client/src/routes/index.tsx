import { createFileRoute, Link } from "@tanstack/react-router";

import {
  AnalysisScreen,
  ArchitectureScreen,
  AssistantScreen,
  ImpactScreen,
  ScaledScreen,
  TracerScreen,
} from "@/components/landing/product-screens";
import landingCss from "@/components/landing/landing.css?url";

const TITLE = "CodeLens AI — Understand a codebase before you change it";
const DESCRIPTION =
  "CodeLens AI maps the architecture, dependencies and reach of every change in your GitHub repository, and answers questions with citations to the code.";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: TITLE },
      { name: "description", content: DESCRIPTION },
      { property: "og:title", content: TITLE },
      { property: "og:description", content: DESCRIPTION },
    ],
    links: [
      { rel: "stylesheet", href: landingCss },
      { rel: "preconnect", href: "https://fonts.googleapis.com" },
      { rel: "preconnect", href: "https://fonts.gstatic.com", crossOrigin: "anonymous" },
      {
        // The page text uses Instrument Sans; the product screens inside it use the app's own faces
        // (Plus Jakarta Sans + JetBrains Mono), which `__root.tsx` already loads.
        rel: "stylesheet",
        href: "https://fonts.googleapis.com/css2?family=Instrument+Sans:wdth,wght@75..100,400..700&display=swap",
      },
    ],
  }),
  component: Landing,
});

const steps = [
  {
    title: "Connect",
    body: "Install the GitHub app, then choose a repository and a branch. Access is read-only.",
  },
  {
    title: "Analyze",
    body: "Files are parsed, and imports, symbols and API routes are extracted.",
  },
  {
    title: "Index",
    body: "The architecture map, search index and debt findings are built for that branch.",
  },
  {
    title: "Explore",
    body: "Browse the map, ask questions, trace requests and check the impact of a commit.",
  },
];

const roles = [
  {
    role: "New developers",
    body: "Follow a reading order for the repository and learn the architecture before touching it.",
  },
  {
    role: "Engineers",
    body: "Trace a request end to end, check the impact of a commit, and ask before interrupting the module owner.",
  },
  {
    role: "Senior developers",
    body: "Find coupling problems and circular dependencies, with evidence for a design review.",
  },
  {
    role: "Tech leads",
    body: "Track technical debt and architecture over time, and see how risky a change is before approving it.",
  },
];

const more = [
  {
    title: "Compare two commits",
    body: "See which files, endpoints and dependencies were added or removed between any two analyzed commits.",
  },
  {
    title: "API reference",
    body: "Every endpoint found in the code, with the file it is declared in and a link straight to its trace.",
  },
  {
    title: "Dependency analysis",
    body: "Find circular dependencies, tightly coupled modules and files that nothing imports.",
  },
  {
    title: "Technical debt",
    body: "Findings for complexity, oversized files, coupling, cycles, missing docs and outdated packages, each with the files behind it.",
  },
  {
    title: "Onboarding paths",
    body: "A suggested reading order for your repository, built from its dependency structure, with progress you can tick off.",
  },
  {
    title: "Documentation",
    soon: true,
    body: "Generated from the code instead of written by hand.",
  },
];

function BrandMark() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path
        d="M3 11.5 8 3l5 8.5"
        fill="none"
        stroke="oklch(0.762 0.163 46)"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <circle cx="8" cy="12.4" r="1.4" fill="oklch(0.648 0.192 41)" />
    </svg>
  );
}

function Landing() {
  return (
    <div className="lp">
      <header className="nav">
        <div className="wrap">
          <a className="brand" href="#top">
            <span className="mark">
              <BrandMark />
            </span>
            CodeLens AI
          </a>
          <nav className="nav-links" aria-label="Sections">
            <a href="#product">Product</a>
            <a href="#how">How it works</a>
            <a href="#teams">Teams</a>
          </nav>
          <div className="nav-end">
            <Link to="/login" className="signin">
              Sign in
            </Link>
            <Link to="/signup" className="btn btn-primary btn-sm">
              Connect GitHub
            </Link>
          </div>
        </div>
      </header>

      <main id="top">
        <section className="band dark hero">
          <div className="wrap">
            <h1>Understand a codebase before you change it.</h1>
            <p className="lead">
              CodeLens AI maps the architecture, dependencies and reach of every change in your
              GitHub repository, and answers questions with citations to the code.
            </p>
            <div className="actions">
              <Link to="/signup" className="btn btn-primary">
                Connect GitHub
              </Link>
              <Link to="/app" className="btn btn-ghost">
                Open the demo
              </Link>
              <span className="note">Read-only access to the repositories you choose.</span>
            </div>

            <ScaledScreen
              width={1440}
              crop={700}
              nativeHeight={820}
              className="stage"
              innerClassName="pf ax"
              label="The CodeLens AI Architecture Explorer with the Auth Service component selected. What it depends on is drawn in orange and what depends on it in blue."
            >
              <ArchitectureScreen />
            </ScaledScreen>
          </div>
        </section>

        <section className="band light pad">
          <div className="wrap">
            <p className="statement">
              Before you can change a system, you have to understand it.{" "}
              <span>CodeLens AI does that part first, and shows its work.</span>
            </p>
          </div>
        </section>

        <section
          className="band light"
          id="product"
          style={{ paddingBottom: "clamp(72px, 10vw, 136px)" }}
        >
          <div className="wrap features">
            <div>
              <div className="feat-head">
                <h3>Ask in plain language. Get answers you can check.</h3>
                <p>
                  Every answer is built from the files in your latest analysis and lists the exact
                  files and lines it used. Open any source to read the code itself.
                </p>
              </div>
              <div className="shot">
                <ScaledScreen
                  width={1280}
                  nativeHeight={760}
                  innerClassName="pf as"
                  label="The AI Assistant answering how authentication works, with the files and lines it used listed beside the answer."
                >
                  <AssistantScreen />
                </ScaledScreen>
              </div>
            </div>

            <div>
              <div className="feat-head">
                <h3>See what a commit touches before it ships.</h3>
                <p>
                  Pick any analyzed commit and follow it through the real import graph to the files
                  and endpoints that depend on it. The risk score shows exactly how it was worked
                  out.
                </p>
              </div>
              <div className="shot">
                <ScaledScreen
                  width={1280}
                  nativeHeight={1000}
                  innerClassName="pf im"
                  label="Impact Analysis of a commit: a high risk score, the files directly and indirectly affected, and the endpoints reached."
                >
                  <ImpactScreen />
                </ScaledScreen>
              </div>
            </div>

            <div>
              <div className="feat-head">
                <h3>Follow a request from route to database.</h3>
                <p>
                  Pick an endpoint or a file and see the files it can reach, layer by layer, from
                  route and controller through service and repository to the model. It is built from
                  imports, so it shows what the code can reach, not what it runs.
                </p>
              </div>
              <div className="shot">
                <ScaledScreen
                  width={1280}
                  nativeHeight={720}
                  innerClassName="pf tr"
                  label="Flow Tracer for POST /api/login, showing the files reached in each layer."
                >
                  <TracerScreen />
                </ScaledScreen>
              </div>
            </div>

            <dl className="also" style={{ marginTop: 0 }}>
              {more.map((m) => (
                <div key={m.title}>
                  <dt>
                    <h3>
                      {m.title}
                      {m.soon ? <span className="soon">Coming soon</span> : null}
                    </h3>
                  </dt>
                  <dd>
                    <p>{m.body}</p>
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        <section className="band dark pad" id="how">
          <div className="wrap">
            <h2 className="sec-title">From repository to answers in four steps.</h2>
            <ol className="steps">
              {steps.map((s, i) => (
                <li key={s.title}>
                  <span className="n">{i + 1}</span>
                  <h3>{s.title}</h3>
                  <p>{s.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="band light pad" id="teams">
          <div className="wrap">
            <h2 className="sec-title">Useful from every seat on the team.</h2>
            <dl className="roles">
              {roles.map((r) => (
                <div key={r.role}>
                  <dt>{r.role}</dt>
                  <dd>{r.body}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>

        <section className="band dark pad closing">
          <div className="wrap closing-grid">
            <div>
              <h2>Connect a repository and watch it get mapped.</h2>
              <p className="sub">
                Choose a repository and a branch. The analysis starts straight away, and you can
                follow each stage as it finishes.
              </p>
              <div className="actions">
                <Link to="/signup" className="btn btn-primary">
                  Connect GitHub
                </Link>
              </div>
            </div>
            <ScaledScreen
              width={520}
              nativeHeight={560}
              innerClassName="pf"
              label="The analysis progress screen: a progress ring at 58 percent and a checklist of stages, with the architecture graph being built."
            >
              <AnalysisScreen />
            </ScaledScreen>
          </div>
        </section>
      </main>

      <footer className="band dark">
        <div className="wrap">
          <div className="foot">
            <div className="about">
              <a className="brand" href="#top">
                <span className="mark">
                  <BrandMark />
                </span>
                CodeLens AI
              </a>
              <p>Codebase intelligence for your GitHub repositories.</p>
            </div>
            <div>
              <h4>Product</h4>
              <ul>
                <li>
                  <a href="#product">Features</a>
                </li>
                <li>
                  <a href="#how">How it works</a>
                </li>
                <li>
                  <a href="#teams">Teams</a>
                </li>
              </ul>
            </div>
            <div>
              <h4>Get started</h4>
              <ul>
                <li>
                  <Link to="/signup">Connect GitHub</Link>
                </li>
                <li>
                  <Link to="/app">Open the demo</Link>
                </li>
                <li>
                  <Link to="/login">Sign in</Link>
                </li>
              </ul>
            </div>
          </div>
          <div className="bottom">
            <span>Read-only access to the repositories you choose.</span>
            <span>© 2026 CodeLens AI</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
