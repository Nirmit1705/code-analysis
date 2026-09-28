export const PRODUCT = {
  name: "CodeLens AI",
  tagline: "Codebase Intelligence",
};

export const repo = {
  name: "hiresight/hiresight-platform",
  short: "HireSight",
  branch: "main",
  branches: [
    { name: "main", commit: "a3f91c2", message: "fix(auth): refresh token rotation window", when: "2h ago" },
    { name: "feature/payment", commit: "7de0b41", message: "feat(billing): stripe subscription webhooks", when: "5h ago" },
    { name: "release/2.4", commit: "c1b7742", message: "chore: bump node to 20.11", when: "2d ago" },
    { name: "fix/interview-latency", commit: "9a02fe5", message: "perf: batch redis reads in scheduler", when: "4d ago" },
  ],
  lastAnalyzed: "14 minutes ago",
  visibility: "private" as const,
  languages: [
    { name: "TypeScript", pct: 68.4, color: "var(--info)" },
    { name: "JavaScript", pct: 16.2, color: "var(--warning)" },
    { name: "Python", pct: 11.1, color: "var(--cyan)" },
    { name: "SQL", pct: 4.3, color: "var(--magenta)" },
  ],
  stats: {
    files: 3184,
    loc: 412_907,
    modules: 47,
    apis: 118,
    dependencies: 264,
    contributors: 23,
  },
};

export const healthMetrics = [
  { key: "architecture", label: "Architecture Health", value: 82, delta: +3, tone: "success" as const, note: "3 boundary violations" },
  { key: "quality", label: "Code Quality", value: 76, delta: -2, tone: "warning" as const, note: "avg complexity 7.4" },
  { key: "dependency", label: "Dependency Health", value: 64, delta: -6, tone: "warning" as const, note: "9 outdated · 2 CVE" },
  { key: "docs", label: "Documentation Coverage", value: 41, delta: +11, tone: "danger" as const, note: "112 undocumented exports" },
  { key: "debt", label: "Technical Debt", value: 72, delta: +4, tone: "warning" as const, note: "moderate" },
];

export type GraphNode = {
  id: string;
  label: string;
  kind: "frontend" | "gateway" | "service" | "data" | "external" | "cache";
  x: number;
  y: number;
  health: number;
  files: number;
  description: string;
  keyFiles: string[];
  apis: string[];
  insight: string;
};

export const graphNodes: GraphNode[] = [
  { id: "web", label: "React Frontend", kind: "frontend", x: 90, y: 210, health: 88, files: 1042, description: "Vite + React 19 SPA. Route-level code splitting, TanStack Query data layer, shared design system in packages/ui.", keyFiles: ["apps/web/src/main.tsx", "apps/web/src/routes/__root.tsx", "apps/web/src/lib/api-client.ts"], apis: ["GET /api/me", "GET /api/interviews"], insight: "37% of API calls bypass api-client.ts and use raw fetch, defeating retry and auth refresh logic." },
  { id: "gateway", label: "API Gateway", kind: "gateway", x: 320, y: 210, health: 79, files: 148, description: "Fastify edge layer handling routing, rate limiting, request tracing and schema validation before service dispatch.", keyFiles: ["apps/gateway/src/server.ts", "apps/gateway/src/plugins/rate-limit.ts", "apps/gateway/src/routes/index.ts"], apis: ["ALL /api/*"], insight: "Rate limit config is duplicated in two plugins with divergent windows (60s vs 120s)." },
  { id: "auth", label: "Auth Service", kind: "service", x: 560, y: 92, health: 61, files: 96, description: "JWT issuance, refresh rotation, RBAC guards and OAuth (GitHub, Google) exchange.", keyFiles: ["services/auth/src/auth.controller.ts", "services/auth/src/auth.service.ts", "services/auth/src/jwt.strategy.ts", "services/auth/src/auth.guard.ts"], apis: ["POST /api/login", "POST /api/register", "POST /api/token/refresh"], insight: "Authentication module has high coupling with three unrelated services (billing, notification, analytics)." },
  { id: "interview", label: "Interview Service", kind: "service", x: 560, y: 210, health: 84, files: 214, description: "Interview scheduling, transcript pipeline, scoring rubric evaluation and candidate feedback generation.", keyFiles: ["services/interview/src/interview.service.ts", "services/interview/src/scoring/rubric.ts", "services/interview/src/scheduler.ts"], apis: ["POST /api/interviews", "GET /api/interviews/:id", "POST /api/interviews/:id/score"], insight: "Scheduler holds Redis connections per request instead of pooling — visible latency spike above 200 rps." },
  { id: "billing", label: "Billing Service", kind: "service", x: 560, y: 328, health: 73, files: 87, description: "Stripe subscription lifecycle, invoice reconciliation, seat metering and webhook ingestion.", keyFiles: ["services/billing/src/billing.service.ts", "services/billing/src/webhooks/stripe.ts"], apis: ["POST /api/billing/checkout", "POST /api/public/webhooks/stripe"], insight: "Webhook handler is not idempotent; duplicate Stripe deliveries can double-count seats." },
  { id: "analytics", label: "Analytics Worker", kind: "service", x: 560, y: 436, health: 69, files: 63, description: "Python ETL worker aggregating interview funnels into warehouse tables on a 15 minute cadence.", keyFiles: ["workers/analytics/pipeline.py", "workers/analytics/models/funnel.py"], apis: ["internal: analytics.aggregate"], insight: "Worker reads directly from the primary Postgres instance, competing with request traffic." },
  { id: "postgres", label: "PostgreSQL", kind: "data", x: 830, y: 150, health: 86, files: 41, description: "Primary relational store. 64 tables, Prisma schema, migrations under db/migrations.", keyFiles: ["db/schema.prisma", "db/migrations/20240915_add_scores.sql"], apis: [], insight: "Two hot tables (interview_events, audit_log) lack composite indexes used by the analytics worker." },
  { id: "redis", label: "Redis", kind: "cache", x: 830, y: 268, health: 91, files: 12, description: "Session cache, rate limit counters, BullMQ job queues for transcript processing.", keyFiles: ["packages/cache/src/redis.ts", "packages/cache/src/queues.ts"], apis: [], insight: "Session TTL (7d) exceeds refresh token lifetime (24h), leaving stale sessions resolvable." },
  { id: "external", label: "External APIs", kind: "external", x: 830, y: 386, health: 74, files: 28, description: "Stripe, GitHub OAuth, Deepgram transcription, SendGrid email and S3 object storage.", keyFiles: ["packages/integrations/src/stripe.ts", "packages/integrations/src/deepgram.ts"], apis: ["stripe.com/v1", "api.deepgram.com/v1/listen"], insight: "Deepgram calls have no timeout configured; a slow upstream blocks the transcript queue." },
];

export const graphEdges: Array<{ from: string; to: string; label?: string; risk?: "high" | "med" }> = [
  { from: "web", to: "gateway", label: "https" },
  { from: "gateway", to: "auth", label: "jwt" },
  { from: "gateway", to: "interview" },
  { from: "gateway", to: "billing" },
  { from: "auth", to: "postgres" },
  { from: "auth", to: "redis" },
  { from: "interview", to: "postgres" },
  { from: "interview", to: "redis" },
  { from: "interview", to: "external", label: "deepgram" },
  { from: "billing", to: "postgres" },
  { from: "billing", to: "external", label: "stripe", risk: "med" },
  { from: "analytics", to: "postgres", risk: "high" },
  { from: "interview", to: "analytics" },
  { from: "auth", to: "billing", risk: "high" },
];

export const recentCommits = [
  { sha: "a3f91c2", message: "fix(auth): refresh token rotation window", author: "m.okafor", components: ["Auth Service", "Redis"], impact: "high", date: "2h ago" },
  { sha: "7de0b41", message: "feat(billing): stripe subscription webhooks", author: "j.lindqvist", components: ["Billing Service", "External APIs"], impact: "high", date: "5h ago" },
  { sha: "5cc21ab", message: "refactor(interview): extract rubric evaluator", author: "s.ramirez", components: ["Interview Service"], impact: "medium", date: "9h ago" },
  { sha: "e91b330", message: "chore(web): migrate list views to TanStack Query", author: "a.deshpande", components: ["React Frontend"], impact: "low", date: "1d ago" },
  { sha: "b7740de", message: "perf(db): add composite index on interview_events", author: "m.okafor", components: ["PostgreSQL", "Analytics Worker"], impact: "medium", date: "1d ago" },
  { sha: "1fa8c04", message: "fix(gateway): normalize rate-limit window to 60s", author: "t.nakamura", components: ["API Gateway"], impact: "low", date: "2d ago" },
];

export const aiInsights = [
  { tone: "danger" as const, title: "Authentication module has high coupling", body: "auth.service.ts is imported by billing, notification and analytics modules that do not require identity primitives. 41 inbound references across 3 bounded contexts." },
  { tone: "warning" as const, title: "Analytics worker bypasses the service layer", body: "workers/analytics/pipeline.py queries the primary Postgres instance directly, sidestepping repository-level access rules and competing with request traffic." },
  { tone: "info" as const, title: "Transcript queue lacks a timeout boundary", body: "Deepgram integration has no request timeout. A single slow upstream response can stall BullMQ transcript workers for the full lock duration." },
];

export const fileTree = [
  {
    name: "apps", type: "dir", children: [
      { name: "web", type: "dir", children: [
        { name: "src/main.tsx", type: "file", lang: "tsx" },
        { name: "src/lib/api-client.ts", type: "file", lang: "ts" },
      ] },
      { name: "gateway", type: "dir", children: [{ name: "src/server.ts", type: "file", lang: "ts" }] },
    ],
  },
  {
    name: "services", type: "dir", children: [
      { name: "auth", type: "dir", children: [
        { name: "src/auth.controller.ts", type: "file", lang: "ts" },
        { name: "src/auth.service.ts", type: "file", lang: "ts" },
        { name: "src/jwt.strategy.ts", type: "file", lang: "ts" },
        { name: "src/auth.guard.ts", type: "file", lang: "ts" },
      ] },
      { name: "interview", type: "dir", children: [
        { name: "src/interview.service.ts", type: "file", lang: "ts" },
        { name: "src/scheduler.ts", type: "file", lang: "ts" },
      ] },
      { name: "billing", type: "dir", children: [{ name: "src/billing.service.ts", type: "file", lang: "ts" }] },
    ],
  },
  { name: "packages", type: "dir", children: [
    { name: "cache/src/redis.ts", type: "file", lang: "ts" },
    { name: "integrations/src/stripe.ts", type: "file", lang: "ts" },
  ] },
  { name: "db", type: "dir", children: [{ name: "schema.prisma", type: "file", lang: "prisma" }] },
] as const;

export const authServiceSource = `import { Injectable, UnauthorizedException } from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';
import { compare } from 'bcryptjs';

import { UserRepository } from '../../user/src/user.repository';
import { RedisClient } from '@hiresight/cache';
import { AuditLogger } from '@hiresight/observability';

const REFRESH_TTL_SECONDS = 60 * 60 * 24;

@Injectable()
export class AuthService {
  constructor(
    private readonly users: UserRepository,
    private readonly jwt: JwtService,
    private readonly redis: RedisClient,
    private readonly audit: AuditLogger,
  ) {}

  async validateCredentials(email: string, password: string) {
    const user = await this.users.findByEmail(email);
    if (!user) throw new UnauthorizedException('INVALID_CREDENTIALS');

    const ok = await compare(password, user.passwordHash);
    if (!ok) {
      await this.audit.record('auth.login.failed', { email });
      throw new UnauthorizedException('INVALID_CREDENTIALS');
    }
    return user;
  }

  async issueTokens(userId: string, orgId: string) {
    const accessToken = await this.jwt.signAsync(
      { sub: userId, org: orgId, scope: 'session' },
      { expiresIn: '15m' },
    );
    const refreshToken = crypto.randomUUID();

    // NOTE: session TTL exceeds refresh lifetime — flagged by CodeLens AI
    await this.redis.set(\`session:\${refreshToken}\`, userId, 'EX', REFRESH_TTL_SECONDS);
    return { accessToken, refreshToken };
  }

  async rotateRefreshToken(token: string) {
    const userId = await this.redis.get(\`session:\${token}\`);
    if (!userId) throw new UnauthorizedException('SESSION_EXPIRED');
    await this.redis.del(\`session:\${token}\`);
    const user = await this.users.findById(userId);
    return this.issueTokens(user.id, user.orgId);
  }
}`;

export const fileInsight = {
  path: "services/auth/src/auth.service.ts",
  purpose: "Owns credential verification, JWT issuance and refresh-token rotation for every authenticated surface of the platform.",
  complexity: { score: 14, label: "High", loc: 212, functions: 7, branches: 23 },
  dependencies: ["user.repository.ts", "@hiresight/cache", "@hiresight/observability", "@nestjs/jwt"],
  usedBy: ["auth.controller.ts", "auth.guard.ts", "billing.service.ts", "notification.service.ts", "analytics.gateway.ts"],
  apis: ["POST /api/login", "POST /api/register", "POST /api/token/refresh"],
  explanation:
    "AuthService is the identity kernel: controllers delegate credential checks here, the guard resolves scopes from tokens minted here, and refresh rotation invalidates the Redis session key before issuing a new pair. Two risks stand out — the Redis session TTL (24h) outlives the 15m access token, and three non-identity services import this class directly instead of consuming the published guard.",
};

export const suggestedQuestions = [
  "How does authentication work?",
  "Trace the payment flow.",
  "Where is Redis used?",
  "Which modules depend on UserService?",
  "What happens when a user registers?",
];

export const traceFlows: Record<string, Array<{ label: string; sub: string; file: string; fn: string; deps: string[]; note: string }>> = {
  "User Registration": [
    { label: "Registration Page", sub: "React route", file: "apps/web/src/routes/register.tsx", fn: "RegisterForm.onSubmit", deps: ["api-client.ts", "zod"], note: "Client validates with a zod schema mirrored from the server contract, then posts through api-client which attaches the org header." },
    { label: "POST /api/register", sub: "API Gateway", file: "apps/gateway/src/routes/auth.ts", fn: "registerRoute", deps: ["rate-limit.ts", "schema-validate.ts"], note: "Rate limited to 5 requests / 60s per IP. Body is validated against RegisterSchema before dispatch." },
    { label: "AuthController", sub: "Auth Service", file: "services/auth/src/auth.controller.ts", fn: "register()", deps: ["auth.service.ts", "dto/register.dto.ts"], note: "Maps the DTO, delegates to AuthService and shapes the 201 response with a session envelope." },
    { label: "AuthService", sub: "Domain logic", file: "services/auth/src/auth.service.ts", fn: "createUser()", deps: ["user.repository.ts", "bcryptjs", "@hiresight/cache"], note: "Hashes the password with bcrypt (cost 12), issues an access/refresh pair and writes the session key to Redis." },
    { label: "UserRepository", sub: "Persistence", file: "services/user/src/user.repository.ts", fn: "insert()", deps: ["prisma", "schema.prisma"], note: "Single transaction inserting into users and org_members, with a unique constraint on (email, org_id)." },
    { label: "PostgreSQL", sub: "Primary store", file: "db/schema.prisma", fn: "model User", deps: [], note: "Row is written to users; a database trigger emits an audit_log entry consumed by the analytics worker." },
  ],
  "Payment Checkout": [
    { label: "Billing Page", sub: "React route", file: "apps/web/src/routes/settings.billing.tsx", fn: "CheckoutButton", deps: ["api-client.ts"], note: "Collects the selected plan and seat count, then requests a Stripe checkout session." },
    { label: "POST /api/billing/checkout", sub: "API Gateway", file: "apps/gateway/src/routes/billing.ts", fn: "checkoutRoute", deps: ["auth.guard.ts"], note: "Requires an authenticated session with the org:admin scope." },
    { label: "BillingService", sub: "Domain logic", file: "services/billing/src/billing.service.ts", fn: "createCheckoutSession()", deps: ["stripe.ts", "auth.service.ts"], note: "Resolves the customer, applies proration and creates the Stripe session. Imports AuthService directly — a coupling violation." },
    { label: "Stripe API", sub: "External", file: "packages/integrations/src/stripe.ts", fn: "checkout.sessions.create", deps: [], note: "Live call to Stripe. Failures are retried twice with exponential backoff." },
    { label: "Stripe Webhook", sub: "Public endpoint", file: "services/billing/src/webhooks/stripe.ts", fn: "handleEvent()", deps: ["billing.service.ts"], note: "Signature-verified. Not idempotent — duplicate deliveries can double-count seats." },
    { label: "PostgreSQL", sub: "Primary store", file: "db/schema.prisma", fn: "model Subscription", deps: [], note: "Subscription and seat rows updated inside one transaction." },
  ],
};

export const dependencyRisks = {
  circular: [
    { a: "auth.service.ts", b: "billing.service.ts", hops: 2, severity: "high" },
    { a: "interview.service.ts", b: "notification.service.ts", hops: 3, severity: "medium" },
  ],
  coupled: [
    { module: "auth", fanIn: 41, fanOut: 12, score: 0.86 },
    { module: "interview", fanIn: 28, fanOut: 19, score: 0.71 },
    { module: "user", fanIn: 33, fanOut: 6, score: 0.64 },
    { module: "billing", fanIn: 11, fanOut: 17, score: 0.58 },
  ],
  orphans: ["packages/legacy-forms/src/wizard.tsx", "services/interview/src/scoring/v1-rubric.ts", "apps/web/src/hooks/useOldFilters.ts"],
  external: [
    { name: "stripe", version: "14.21.0", latest: "18.2.0", severity: "warning", usedBy: 6 },
    { name: "jsonwebtoken", version: "8.5.1", latest: "9.0.2", severity: "danger", usedBy: 4, cve: "CVE-2022-23529" },
    { name: "prisma", version: "5.19.1", latest: "6.4.1", severity: "warning", usedBy: 21 },
    { name: "bullmq", version: "5.12.0", latest: "5.34.2", severity: "ok", usedBy: 3 },
    { name: "axios", version: "0.27.2", latest: "1.7.9", severity: "danger", usedBy: 9, cve: "CVE-2023-45857" },
    { name: "react", version: "19.0.0", latest: "19.2.0", severity: "ok", usedBy: 1 },
  ],
};

export const debtIssues = [
  {
    id: "DEBT-114", severity: "critical", title: "Circular dependency between Auth and Billing",
    category: "Circular Dependencies",
    files: ["services/auth/src/auth.service.ts", "services/billing/src/billing.service.ts"],
    why: "Both modules import each other at construction time. Any change to token scopes forces a redeploy of billing, and the cycle blocks independent testing of either service.",
    fix: "Extract the shared scope resolution into packages/identity and have both services depend on the package instead of each other. Estimated 1.5 days, touches 9 files.",
  },
  {
    id: "DEBT-108", severity: "critical", title: "jsonwebtoken 8.5.1 carries a known CVE",
    category: "Outdated Dependencies",
    files: ["services/auth/src/jwt.strategy.ts", "package.json"],
    why: "CVE-2022-23529 permits key confusion when the secret is passed as an object. Four modules verify tokens with this library.",
    fix: "Upgrade to 9.0.2 and pin the algorithm allowlist to ['RS256']. Verify jwt.strategy.ts passes a Buffer secret.",
  },
  {
    id: "DEBT-097", severity: "high", title: "Duplicated pagination logic across 7 repositories",
    category: "Code Duplication",
    files: ["services/user/src/user.repository.ts", "services/interview/src/interview.repository.ts", "+5 more"],
    why: "Each repository reimplements cursor encoding. Two variants already disagree on how null cursors are handled, which surfaced as a paging bug in the candidate list.",
    fix: "Introduce a shared createCursorPaginator() in packages/db and migrate repositories incrementally behind unit tests.",
  },
  {
    id: "DEBT-091", severity: "high", title: "interview.service.ts exceeds complexity budget",
    category: "High Complexity",
    files: ["services/interview/src/interview.service.ts"],
    why: "Cyclomatic complexity of 34 in scheduleInterview(). The function handles timezone resolution, conflict detection, notification fan-out and rubric assignment.",
    fix: "Split into SchedulingPolicy, ConflictResolver and NotificationDispatcher. Each unit is independently testable.",
  },
  {
    id: "DEBT-083", severity: "medium", title: "Analytics worker queries the primary database",
    category: "Large Modules",
    files: ["workers/analytics/pipeline.py"],
    why: "ETL scans compete with request traffic. p95 read latency rises 40ms during the 15 minute aggregation window.",
    fix: "Point the worker at the read replica and add a composite index on interview_events(org_id, created_at).",
  },
  {
    id: "DEBT-076", severity: "medium", title: "112 exported symbols lack documentation",
    category: "Documentation Gaps",
    files: ["packages/ui/src/index.ts", "services/interview/src/scoring/rubric.ts", "+18 more"],
    why: "New engineers rely on reading implementations to understand public contracts, which is the largest reported onboarding cost in this repository.",
    fix: "Generate TSDoc from CodeLens AI's inferred contracts and commit the reviewed output alongside each package barrel file.",
  },
];

export const debtBreakdown = [
  { label: "Code Duplication", score: 68, weight: "12 clusters" },
  { label: "High Complexity", score: 61, weight: "23 functions" },
  { label: "Circular Dependencies", score: 44, weight: "2 cycles" },
  { label: "Large Modules", score: 74, weight: "5 modules > 1k LOC" },
  { label: "Outdated Dependencies", score: 52, weight: "9 outdated · 2 CVE" },
  { label: "Documentation Gaps", score: 41, weight: "112 exports" },
];

export const debtTrend = [
  { month: "Mar", score: 58, debt: 42 },
  { month: "Apr", score: 61, debt: 39 },
  { month: "May", score: 60, debt: 40 },
  { month: "Jun", score: 65, debt: 35 },
  { month: "Jul", score: 68, debt: 32 },
  { month: "Aug", score: 72, debt: 28 },
];

export const apiEndpoints = [
  { method: "POST", path: "/api/login", auth: "public", service: "Auth Service", body: "{ email: string, password: string }", response: "{ accessToken, refreshToken, user }", files: ["auth.controller.ts", "auth.service.ts"], usedBy: ["apps/web/src/routes/login.tsx"], calls: "24.1k / day" },
  { method: "POST", path: "/api/register", auth: "public", service: "Auth Service", body: "{ email, password, orgName }", response: "{ user, session }", files: ["auth.controller.ts", "user.repository.ts"], usedBy: ["apps/web/src/routes/register.tsx"], calls: "1.3k / day" },
  { method: "POST", path: "/api/token/refresh", auth: "refresh token", service: "Auth Service", body: "{ refreshToken: string }", response: "{ accessToken, refreshToken }", files: ["auth.controller.ts", "jwt.strategy.ts"], usedBy: ["apps/web/src/lib/api-client.ts"], calls: "88.4k / day" },
  { method: "GET", path: "/api/interviews", auth: "bearer", service: "Interview Service", body: "—", response: "{ items: Interview[], cursor }", files: ["interview.controller.ts", "interview.repository.ts"], usedBy: ["apps/web/src/routes/interviews.index.tsx"], calls: "63.7k / day" },
  { method: "POST", path: "/api/interviews/:id/score", auth: "bearer · org:reviewer", service: "Interview Service", body: "{ rubricId, scores: Score[] }", response: "{ interview, summary }", files: ["interview.controller.ts", "scoring/rubric.ts"], usedBy: ["apps/web/src/routes/interviews.$id.review.tsx"], calls: "4.9k / day" },
  { method: "POST", path: "/api/billing/checkout", auth: "bearer · org:admin", service: "Billing Service", body: "{ planId, seats }", response: "{ checkoutUrl }", files: ["billing.controller.ts", "stripe.ts"], usedBy: ["apps/web/src/routes/settings.billing.tsx"], calls: "210 / day" },
];

export const impactTargets = [
  "services/auth/src/auth.service.ts",
  "services/interview/src/interview.service.ts",
  "services/billing/src/webhooks/stripe.ts",
  "packages/cache/src/redis.ts",
  "apps/gateway/src/plugins/rate-limit.ts",
];

export const analysisHistory = [
  { id: "AN-2291", branch: "main", commit: "a3f91c2", files: 3184, debt: 72, arch: 82, date: "Aug 17, 2026 · 11:52" },
  { id: "AN-2287", branch: "feature/payment", commit: "7de0b41", files: 3211, debt: 68, arch: 76, date: "Aug 17, 2026 · 08:14" },
  { id: "AN-2280", branch: "main", commit: "5cc21ab", files: 3170, debt: 71, arch: 81, date: "Aug 16, 2026 · 19:03" },
  { id: "AN-2271", branch: "release/2.4", commit: "c1b7742", files: 3096, debt: 66, arch: 79, date: "Aug 15, 2026 · 09:41" },
  { id: "AN-2264", branch: "main", commit: "b7740de", files: 3081, debt: 65, arch: 78, date: "Aug 14, 2026 · 16:22" },
];

export const githubRepos = [
  { name: "hiresight/hiresight-platform", lang: "TypeScript", branch: "main", updated: "2 hours ago", private: true, stars: 0, size: "184 MB" },
  { name: "hiresight/hiresight-infra", lang: "HCL", branch: "main", updated: "1 day ago", private: true, stars: 0, size: "12 MB" },
  { name: "hiresight/transcript-worker", lang: "Python", branch: "develop", updated: "3 days ago", private: true, stars: 0, size: "31 MB" },
  { name: "hiresight/design-system", lang: "TypeScript", branch: "main", updated: "6 days ago", private: false, stars: 214, size: "22 MB" },
  { name: "m-okafor/fastify-tracing", lang: "TypeScript", branch: "main", updated: "2 weeks ago", private: false, stars: 87, size: "3 MB" },
  { name: "hiresight/candidate-portal", lang: "JavaScript", branch: "main", updated: "1 month ago", private: true, stars: 0, size: "58 MB" },
];
