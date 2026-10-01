# JackVerse Caseworker: Consumer Web Architecture
**Phase 4 Delivery Specification**

---

## 1. Architectural Overview & Technical Stack

The JackVerse Caseworker Web Interface is a modern, client-side Single Page Application (SPA) designed to interface with the deterministic Caseworker API runtime established in Phase 3.

### Core Stack
- **Framework**: React 19 + TypeScript + Vite 6
- **Routing**: React Router DOM v7 (client-side HTML5 history routing with Nginx fallback)
- **Data & Mutation State**: TanStack React Query v5 (in-memory caching, dynamic key invalidation, zero query leakage across users)
- **Styling**: Tailwind CSS v3 with semantic design tokens (`web/src/styles/tokens.css`)
- **Icons**: Lucide React (geometric, stroke-width calibrated to 1.5–2px)
- **Testing**:
  - Unit & Component: Vitest + React Testing Library + jsdom
  - End-to-End & Visual Audit: Playwright (Desktop 1440x900 and Mobile 375x812)

---

## 2. Numbered Destination Structure

The navigation rail implements an editorial numbered hierarchy (`01`–`06`):

| # | Route | View Component | Operational Purpose |
| :- | :--- | :--- | :--- |
| **01** | `/` | `HomeView` | Editorial intent dispatch prompt + active mission index |
| **02** | `/missions` | `MissionsView` | Full missions dossier, filtering, inline initialization |
| **02** | `/missions/:id` | `MissionDetailView` | Mission workspace, case lists, claims, audit stream |
| **—** | `/cases/:caseId` | `CaseDetailView` | Granular case workspace, state machine transitions, resolution |
| **03** | `/opportunities` | `OpportunitiesView` | Raw opportunity index, scoring metrics, detail inspector |
| **04** | `/approvals` | `NeedsYouView` | Constitutional human-in-the-loop gate, pending approvals, drag-to-authorize |
| **05** | `/context` | `ContextView` | Verifiable personal context vault, profile readiness, on-demand fact decryption |
| **06** | `/activity` | `ActivityView` | Monotonic audit event stream ledger |

---

## 3. Concurrency & Mutation Lifecycle (HTTP ETags)

To prevent lost updates across concurrent workers or multi-tab usage, all mutating endpoints participate in an optimistic concurrency control protocol:

1. **ETag Capture**:
   Every entity retrieval (`GET /cases/{id}`, `GET /missions/{id}`) captures the entity `ETag` response header (e.g. `"case:uuid:v1"`).
2. **If-Match Verification**:
   Mutations (`POST /cases/{id}/transition`, `POST /cases/{id}/actions`, `POST /approvals/{id}/approve`) pass the captured ETag in the `If-Match` header.
3. **412 Precondition Failed Handling**:
   If the resource was modified by another transaction, the API returns HTTP 412. The client catches `PreconditionFailedError` and triggers a clear user warning:
   > *"This changed elsewhere. We've loaded the latest version."*
   The stale query cache is invalidated immediately via `queryClient.invalidateQueries`.

---

## 4. Context Privacy & Sensitive Fact Handling

In strict compliance with Phase 3 and Phase 4.1 privacy boundaries:
1. **Summary Fact Listing (`GET /api/v1/context/facts`)**:
   Returns `FactSummaryResponse` items where raw `value` and `source_reference` are omitted for sensitive facts. SQLite stores facts as plain JSON text; the privacy boundary is server-side projection. No false claims of at-rest encryption or on-demand cryptographic decryption are made.
2. **Masked Presentation**:
   Sensitive facts display a masked placeholder (`PRIVATE ••••••••••••`).
3. **On-Demand Detail Retrieval**:
   Only upon clicking `[ Reveal ]` is a single-fact call issued to `GET /api/v1/context/facts/{id}` to fetch the protected detail.
4. **Zero-Retention Cache**:
   Sensitive detail queries are configured with `staleTime: 0, gcTime: 0`. Clicking `[ Mask ]` or unmounting the component immediately purges the sensitive detail query from `queryClient`.
5. **Metadata-Driven Fact Lifecycle**:
   Fact verification and rejection operate strictly from summary metadata (`fact_id` and `version`) using deterministic ETag matching, without requiring raw value revelation.

---

## 5. Docker Compose & Normalized Port 3001

To prevent port collision with Grafana (which operates on port 3000), the web application origin is normalized to **port 3001** across all environments:

| Environment | Host Port | Target / Service |
| :--- | :--- | :--- |
| **Vite Dev Server** | `http://127.0.0.1:3001` | Local development with HMR (`npm run dev`) |
| **Vite Preview** | `http://127.0.0.1:3001` | Local production bundle verification (`npm run preview`) |
| **Docker Compose** | `http://127.0.0.1:3001` | `caseworker-web` Nginx container |

### Docker Service Configuration
- **`caseworker-web`**: Built from `web/Dockerfile` with multi-stage build args (`VITE_JACKVERSE_DEV_AUTH`, `VITE_ENABLE_PROTOTYPES`, `VITE_CASEWORKER_API_URL`).
- **`docker-compose.yml`**: Exposes `127.0.0.1:${CASEWORKER_WEB_PORT:-3001}:80`, proxying `/api` and `/health` to `caseworker-api`.
- **`docker-compose.dev.yml`**: Configures `CASEWORKER_ALLOWED_ORIGINS` to permit `http://localhost:3001,http://127.0.0.1:3001`.

---

## 6. Font Bundling & Licensing

To eliminate third-party CDN tracking and maintain deterministic offline capability, typography is locally bundled via npm:

| Font Family | NPM Package | License | Distribution |
| :--- | :--- | :--- | :--- |
| **Instrument Serif** | `@fontsource/instrument-serif` | SIL Open Font License 1.1 | Local WOFF/WOFF2 static assets |
| **Geist Sans** | `@fontsource/geist-sans` | SIL Open Font License 1.1 | Local WOFF/WOFF2 static assets |
| **Geist Mono** | `@fontsource/geist-mono` | SIL Open Font License 1.1 | Local WOFF/WOFF2 static assets |

Zero external requests to Google Fonts, Adobe Typekit, or CDNs occur at runtime.

---

## 7. OpenAPI Contract Pipeline & Type Derivation

Phase 4.1 establishes `src/caseworker/api` as the single authoritative source of truth:

1. **Deterministic Schema Export**:
   `scripts/export_caseworker_openapi.py` instantiates `create_app()` and dumps the OpenAPI 3.1 specification to `web/openapi.json` without requiring a running server.
2. **Type Generation**:
   `npm run api:generate` invokes `openapi-typescript` to produce `web/src/api/generated-schema.d.ts`.
3. **Canonical Type Aliases**:
   `web/src/api/types.ts` imports directly from `components['schemas']` in `generated-schema.d.ts`, guaranteeing that domain models, enums (`MissionKind`, `ActionStatus`, `RiskLevel`), and schemas never drift out of sync.
4. **CI Contract Guard**:
   `npm run api:check` generates schema and asserts zero git diff (`git diff --exit-code -- openapi.json src/api/generated-schema.d.ts`), failing the build immediately if API contracts drift.

---

## 8. Real Browser Playwright Integration Suite

Phase 4.1 replaces mocked shell tests with a full end-to-end browser integration suite running against a live FastAPI server:

- **Orchestration**: Playwright configuration (`web/playwright.config.ts`) manages dual servers:
  1. Live FastAPI backend on `http://127.0.0.1:8089` with `CASEWORKER_DEV_AUTH=true` backed by an isolated temporary database (`/tmp/caseworker_e2e_integration.db`).
  2. Vite dev server on `http://127.0.0.1:3001` with `VITE_JACKVERSE_DEV_AUTH=true`, `VITE_ENABLE_PROTOTYPES=true`, and proxying to port 8089.
- **Teardown Cleanup**: `web/tests/e2e/global-teardown.ts` unlinks temporary database and WAL/SHM artifacts after test execution.
- **Automated Flows Tested (`tests/e2e/caseworker_real_integration.spec.ts`)**:
  - **Flow A (Identity Isolation)**: Alice creates mission; Bob switches identity -> Alice's mission absent from list; direct navigation gives 404.
  - **Flow B (Mission Flow)**: Validates all 3 authoritative `MissionKind` values (`opportunity_pursuit`, `problem_resolution`, `general_goal`) and state transitions (`DRAFT -> ACTIVE -> PAUSED -> ACTIVE -> COMPLETED`) with real server ETags.
  - **Flow C (Case Flow)**: Case creation under Mission, progression across domain state machine (`NEW -> INTAKE -> INVESTIGATING`) with ETag preconditions.
  - **Flow D (Opportunity Flow)**: Manual prospect ingress, schema field verification, and authoritative state machine transitions (`DISCOVERED -> EVALUATING -> SHORTLISTED`).
  - **Flow E (Context Flow)**: Sensitive fact masking (`PRIVATE ••••••••••••`), on-demand reveal, cache purging on mask, and non-revealing fact verification.
  - **Flow F (Claim Flow)**: Strict claim verification policy satisfaction (active, purpose-authorized, `USER_VERIFIED` context fact) leading to `SUPPORTED` claim status.
  - **Flow G (Approval Governance)**: Proposes consequential action (`submit_form`, HIGH risk) -> requests approval under Action ETag -> inspects in "Needs You" -> slides to authorize with Approval ETag -> verifies `APPROVED` status and truthful execution connection message.
  - **Flow H (Stale ETag Concurrency)**: Out-of-band mutation bumps backend version -> UI submission returns HTTP 412 -> displays concurrency notice -> refetches latest state without silent retry loop.
