# JackVerse Caseworker API Specification

The **JackVerse Caseworker API** exposes the autonomous caseworker agent runtime to client applications, user interfaces, and external systems. It enforces strict multi-tenant isolation, cryptographically bound human authorization, server-side risk evaluation, optimistic concurrency via HTTP ETags, and standardized RFC 9457 Problem Details.

---

## 1. Architectural Principles

1. **Strict User Isolation**:
   - The authenticated identity (`Principal.user_id`) is strictly derived server-side from authentication headers or credentials.
   - Request bodies NEVER accept `user_id`. Attempting to send `user_id` or other server-owned fields returns `422 Unprocessable Content`.
   - Access to resources owned by another user returns `404 Not Found` rather than `403 Forbidden` to prevent object enumeration.
2. **Resource-Bound ETags & Mutating Preconditions**:
   - Every read and write response includes an `ETag` header formatted as:
     ```http
     ETag: "{resource_type}:{resource_id}:v{version}"
     ```
   - Mutating operations (`transition`, `resolve`, `verify`, `reject`, `supersede`, `approve`, etc.) **require** an `If-Match` request header.
   - Missing `If-Match` header yields `428 Precondition Required`.
   - Mismatched `If-Match` header yields `412 Precondition Failed`.
3. **Server-Side Safety & Action Policy**:
   - Consequential action proposals (`POST /api/v1/cases/{case_id}/actions`) pass through `ActionPolicy.evaluate()`.
   - The server autonomously determines `risk_level` and `requires_approval`. Clients cannot downgrade risk or bypass human approvals.
   - Custom or unrecognized action types fail closed with `requires_approval=True`.
4. **Idempotent Human Approvals**:
   - `Approval` entities are cryptographically bound to the SHA-256 fingerprint of the target `Action`.
   - Calling `POST /api/v1/approvals/{id}/approve` on an already approved action safely and idempotently returns the existing state without side-effects or errors.
   - Attempting to contradict an existing decision (e.g. approving a rejected approval or rejecting an approved approval) raises `409 Conflict`.
5. **Standardized RFC 9457 Problem Details**:
   - Every non-2xx HTTP response returns `Content-Type: application/problem+json`.
   - Error payloads include `type`, `title`, `status`, `detail`, `instance`, and structured extension fields.
6. **Monotonic Event Cursor Streaming**:
   - `GET /api/v1/events` provides cursor pagination powered by native monotonic SQLite positions (`rowid`).
   - Clients supply `after_position=<int>` and receive chronological event streams with `has_more` and `next_position`.

---

## 2. Authentication & Security

### Fail-Closed Dev Auth
- Controlled via `CASEWORKER_DEV_AUTH=true|false`.
- In production (`CASEWORKER_DEV_AUTH=false`), dev headers are ignored and rejected with `401 Unauthorized`.
- When enabled in local development (`CASEWORKER_DEV_AUTH=true`), the caller must send:
  ```http
  X-JackVerse-User: <user_id>
  ```
  or:
  ```http
  Authorization: Bearer dev:<user_id>
  ```
- User identifiers are validated against `^[a-zA-Z0-9_\-\.]{1,128}$`.

### Response Security Headers
Every HTTP response includes:
```http
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Cache-Control: no-store, no-cache, must-revalidate, max-age=0
Pragma: no-cache
X-Request-ID: <uuid>
```

---

## 3. Endpoints Reference

### Health & Readiness
| Method | Path | Description |
|---|---|---|
| `GET` | `/health/live` | Lightweight liveness probe (no DB query). |
| `GET` | `/health/ready` | Lightweight readiness probe (`SELECT 1`). Returns 200 or 503. |

### Missions (`/api/v1/missions`)
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/missions` | Create a new user mission. Returns `201 Created` with ETag. |
| `GET` | `/api/v1/missions` | List user missions (paginated: `limit`, `offset`, `status_filter`). |
| `GET` | `/api/v1/missions/{id}` | Get mission details by ID. Returns ETag or `404`. |
| `POST` | `/api/v1/missions/{id}/transition` | Transition status (`new_status`, `reason`). Requires `If-Match`. |
| `POST` | `/api/v1/missions/{id}/pause` | Pause active mission. Requires `If-Match`. |
| `POST` | `/api/v1/missions/{id}/resume` | Resume paused mission. Requires `If-Match`. |

### Cases (`/api/v1/cases`)
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/cases` | Create a standalone or mission-linked case. Returns `201 Created` with ETag. |
| `POST` | `/api/v1/missions/{id}/cases` | Create case explicitly linked to a mission. Returns `201 Created`. |
| `GET` | `/api/v1/missions/{id}/cases` | List cases under a mission. |
| `GET` | `/api/v1/cases` | List all cases for authenticated user. |
| `GET` | `/api/v1/cases/{id}` | Get case details. Returns ETag or `404`. |
| `POST` | `/api/v1/cases/{id}/transition` | Transition case state machine. Requires `If-Match`. |
| `POST` | `/api/v1/cases/{id}/resolve` | Resolve case with recorded `outcome`. Requires `If-Match`. |

### Opportunities (`/api/v1/opportunities`)
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/opportunities` | Discover opportunity. Returns `201 Created` if newly discovered, or `200 OK` if deduplicated for this user. |
| `GET` | `/api/v1/opportunities` | List user opportunities (paginated). |
| `GET` | `/api/v1/opportunities/{id}` | Get opportunity details. Returns ETag or `404`. |
| `POST` | `/api/v1/opportunities/{id}/transition` | Transition opportunity status. Requires `If-Match`. |

### Context Vault (`/api/v1/context`)
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/context/sources` | Register a provenance source. Returns `SafeSourceResponse` (redacting credentials). |
| `GET` | `/api/v1/context/sources` | List registered context sources. |
| `GET` | `/api/v1/context/sources/{id}` | Get sanitized context source. Returns ETag or `404`. |
| `POST` | `/api/v1/context/facts` | Record a context fact in the user's vault. Returns `201 Created`. |
| `GET` | `/api/v1/context/facts` | List active facts (optional `namespace` filter). |
| `GET` | `/api/v1/context/facts/{id}` | Get fact details. Returns ETag or `404`. |
| `POST` | `/api/v1/context/facts/{id}/verify` | Verify fact (`status`). Requires `If-Match`. |
| `POST` | `/api/v1/context/facts/{id}/reject` | Reject fact (`reason`). Requires `If-Match`. |
| `POST` | `/api/v1/context/facts/{id}/supersede` | Supersede fact with new value. Requires `If-Match`. Returns new fact. |
| `POST` | `/api/v1/context/packages` | Assemble purpose-scoped, policy-gated context package with audit event logging. |

### Claim Ledger (`/api/v1/claims`)
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/claims` | Propose factual claim backed by supporting vault facts. Returns `201 Created`. |
| `GET` | `/api/v1/claims` | List claims belonging to user. |
| `GET` | `/api/v1/claims/{id}` | Get claim details. Returns ETag or `404`. |
| `POST` | `/api/v1/claims/{id}/evaluate` | Re-evaluate claim against active vault facts. Requires `If-Match`. |
| `POST` | `/api/v1/claims/{id}/reject` | Reject claim with recorded rationale. Requires `If-Match`. |

### Actions & Approvals
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/cases/{case_id}/actions` | Propose an action for a case. Evaluates `ActionPolicy` server-side. |
| `GET` | `/api/v1/cases/{case_id}/actions` | List actions for a case. |
| `GET` | `/api/v1/actions/{action_id}` | Get action details. Returns ETag or `404`. |
| `POST` | `/api/v1/actions/{action_id}/request-approval` | Request human approval. Requires `If-Match`. Sets status to `awaiting_approval`. |
| `GET` | `/api/v1/approvals` | List approvals for authenticated user. |
| `GET` | `/api/v1/approvals/{approval_id}` | Get approval details. Returns ETag or `404`. |
| `POST` | `/api/v1/approvals/{approval_id}/approve` | Approve action (idempotent if already approved). Requires `If-Match`. |
| `POST` | `/api/v1/approvals/{approval_id}/reject` | Reject action (idempotent if already rejected). Requires `If-Match`. |

### Domain Events Cursor Stream (`/api/v1/events`)
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/events` | Stream user domain events chronologically. Parameters: `limit` (max 100), `after_position`, `aggregate_type`, `aggregate_id`. |

---

## 4. Problem Details Format (RFC 9457)

All errors return JSON with MIME type `application/problem+json`:

```json
{
  "type": "urn:caseworker:error:precondition-required",
  "title": "Precondition Required",
  "status": 428,
  "detail": "Precondition Required: 'If-Match' header with resource ETag is required for mutating operations.",
  "instance": "urn:request:d4e9c71a-821e-4476-b8a7-041a303ba99d"
}
```
