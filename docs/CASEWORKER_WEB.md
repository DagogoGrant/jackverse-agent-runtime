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

In strict compliance with Phase 3 privacy boundaries:
1. **Summary Fact Listing (`GET /api/v1/context/facts`)**:
   Returns `FactSummaryResponse` items where raw `value` and `source_reference` are omitted for sensitive facts.
2. **Masked Presentation**:
   Sensitive facts display a masked placeholder (`PRIVATE ••••••••••••`).
3. **On-Demand Decryption**:
   Only upon clicking `[ Reveal ]` is a single-fact call issued to `GET /api/v1/context/facts/{id}`.
4. **Zero Local Persistence**:
   Sensitive fact values are held solely in volatile React state. They are **never persisted** to `localStorage` or `sessionStorage`.

---

## 5. Docker Compose & Environment Split

To guarantee security in production while retaining rapid local ergonomics, Docker configurations are strictly divided:

### Base Production Compose (`docker-compose.yml`)
- Dev auth disabled: `CASEWORKER_DEV_AUTH=false`.
- Identity derived strictly from authenticated claims.
- Ports normalized to loopback interface: `127.0.0.1:${CASEWORKER_API_PORT:-8088}:8080`.
- Frontend container serves static assets via Nginx.

### Developer Compose Override (`docker-compose.dev.yml`)
- Dev auth enabled: `CASEWORKER_DEV_AUTH=true`.
- CORS permits `http://localhost:3000`, `http://127.0.0.1:3000`, `http://localhost:4173`.
- Vite dev server runs with hot module reloading.
- Diagnostic header switcher rendered in web shell to toggle identities (`alice` / `bob` / `charlie`).
- Switching identity emits `jv:dev_user_change` and invokes `queryClient.clear()` to completely wipe stale cross-user cache.

---

## 6. Font Bundling & Licensing

To eliminate third-party CDN tracking and maintain deterministic offline capability, typography is locally bundled via npm:

| Font Family | NPM Package | License | Distribution |
| :--- | :--- | :--- | :--- |
| **Instrument Serif** | `@fontsource/instrument-serif` | SIL Open Font License 1.1 | Local WOFF/WOFF2 static assets |
| **Geist Sans** | `@fontsource/geist-sans` | SIL Open Font License 1.1 | Local WOFF/WOFF2 static assets |
| **Geist Mono** | `@fontsource/geist-mono` | SIL Open Font License 1.1 | Local WOFF/WOFF2 static assets |

Zero external requests to Google Fonts, Adobe Typekit, or CDNs occur at runtime.
