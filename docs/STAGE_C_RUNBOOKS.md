# JackVerse Phase 5.3.3.2 Stage C Runbooks

This document contains the operator runbooks for JackVerse Phase 5.3.3.2 Stage C:
1. [OAuth Credential Encryption-Key Migration Runbook](#1-oauth-credential-encryption-key-migration-runbook)
2. [One-Time Governed Gmail Dispatch Runbook](#2-one-time-governed-gmail-dispatch-runbook)

---

## 1. OAuth Credential Encryption-Key Migration Runbook

### Purpose
Rotate the local encryption key used to protect stored OAuth refresh and access tokens at rest in the SQLite database without losing active credentials or modifying external execution plan fingerprints.

> [!IMPORTANT]
> **Token Invalidation Scope**:
> Local key rotation re-encrypts database ciphertexts with a newly generated primary key. It does **not** revoke tokens on Google's authorization servers. If credential exposure is suspected, revoke the token directly via Google Account Permissions or the Google OAuth revocation endpoint.

### Pre-conditions
- Case `case-controlled-test-5332` is in `action_required`.
- Action `act_91966280c188` is `approved`.
- Approval `65cb25bb-0539-4f61-917c-46cf4a84f9dd` is `approved`.
- Plan `plan-d4819278ab3f45af` fingerprint is `6e46f1a5cef7364a843c4b8e8713fa05a0506673a41815597b55107fa729a0ae`.
- Execution attempts count = 0.
- Execution receipts count = 0.

### Step 1: Maintenance Window Isolation
Stop `caseworker-api` to prevent any concurrent database writers:
```bash
docker compose stop caseworker-api
```

### Step 2: Pre-Migration Encryption and State Audit
Inspect current migration state (`NOT_STARTED` expected) using the isolated maintenance container (`network_mode: none`):
```bash
docker compose -f docker-compose.yml -f docker-compose.maintenance.yml run --rm caseworker-maintenance \
  python3 scripts/rotate_oauth_encryption_key.py \
  --db-path /app/.caseworker/caseworker.db \
  --env-file /host_repo/.env.caseworker-api.local \
  --backup-dir /host_repo/backups/oauth_key_rotation \
  --check
```
Expected output:
- `Migration state: NOT_STARTED`
- `Records count: 1`
- `Access Decrypt: True, Refresh Decrypt: True`

### Step 3: Execute Migration
Execute the crash-recoverable key rotation:
```bash
docker compose -f docker-compose.yml -f docker-compose.maintenance.yml run --rm caseworker-maintenance \
  python3 scripts/rotate_oauth_encryption_key.py \
  --db-path /app/.caseworker/caseworker.db \
  --env-file /host_repo/.env.caseworker-api.local \
  --backup-dir /host_repo/backups/oauth_key_rotation \
  --migrate
```
Expected output:
- `Migration status: SUCCESS`
- `Verified DB backup: /host_repo/backups/oauth_key_rotation/caseworker_backup_...db`
- `Verified env backup: /host_repo/backups/oauth_key_rotation/env_caseworker_api_local_...bak`
- `Recovery manifest: /host_repo/backups/oauth_key_rotation/recovery_manifest_...json`
- `Migrated records: 1`
- `New key fingerprint: <16-char-sha256>`

### Step 4: Verification
Confirm completed migration state and dual-token decryption:
```bash
docker compose -f docker-compose.yml -f docker-compose.maintenance.yml run --rm caseworker-maintenance \
  python3 scripts/rotate_oauth_encryption_key.py \
  --db-path /app/.caseworker/caseworker.db \
  --env-file /host_repo/.env.caseworker-api.local \
  --backup-dir /host_repo/backups/oauth_key_rotation \
  --check
```
Expected output:
- `Migration state: COMPLETED`
- `Access Decrypt: True, Refresh Decrypt: True`

### Step 5: Resume Service
Start `caseworker-api` and verify TLS readiness:
```bash
docker compose up -d caseworker-api
curl --cacert certs/localhost.crt https://localhost:3443/health/ready
```

### Rollback / Recovery Procedures (If Interrupted)
If migration was interrupted by a crash:
```bash
# Auto-resume forward recovery or safely clean aborted staging:
docker compose -f docker-compose.yml -f docker-compose.maintenance.yml run --rm caseworker-maintenance \
  python3 scripts/rotate_oauth_encryption_key.py \
  --db-path /app/.caseworker/caseworker.db \
  --env-file /host_repo/.env.caseworker-api.local \
  --backup-dir /host_repo/backups/oauth_key_rotation \
  --resume
```

To restore from a verified matching backup set (strictly refused if newer execution records exist):
```bash
docker compose -f docker-compose.yml -f docker-compose.maintenance.yml run --rm caseworker-maintenance \
  python3 scripts/rotate_oauth_encryption_key.py \
  --db-path /app/.caseworker/caseworker.db \
  --env-file /host_repo/.env.caseworker-api.local \
  --restore-backup /host_repo/backups/oauth_key_rotation/caseworker_backup_<timestamp>_<id>.db \
  --confirm-restore-backup caseworker_backup_<timestamp>_<id>.db
```

---

## 2. One-Time Governed Gmail Dispatch Runbook

### Purpose
Execute the single authorized internal test transmission (`act_91966280c188`) to `dagogograntjack@gmail.com` using dedicated Compose override barriers, trusted TLS verification, explicit operator confirmation, and scoped disarm.

> [!WARNING]
> **No Blind Retries**: If the execute request times out in flight (`AMBIGUOUS_TIMEOUT`), do **not** re-run the dispatch command or force-recreate the container. Check `execution_attempts` and `execution_receipts` first. A timed-out request may have already reached Gmail.

### Pre-conditions Checklist
- [ ] Credential migration completed and verified (`--check` returns `COMPLETED`).
- [ ] Zero execution attempts in database (`total: 0`).
- [ ] Zero execution receipts in database (HTTP 404 resource-not-found).
- [ ] Human approval `65cb25bb-0539-4f61-917c-46cf4a84f9dd` intact.
- [ ] Plan fingerprint matches `6e46f1a5cef7364a843c4b8e8713fa05a0506673a41815597b55107fa729a0ae`.
- [ ] Both execution flags in base deployment are confirmed `false`.

### Step 1: Preflight Audit (Dry Run)
Verify governance chain-of-custody over trusted TLS without transmitting:
```bash
python3 scripts/guarded_stage_c_dispatch.py --dry-run
```
Expected output:
- `[PASS]` Signed operator bearer token generated
- `[PASS]` Complete governance chain-of-custody verified over trusted TLS
- `Action ID: act_91966280c188 (Status: approved, Version: 3)`
- `Action ETag: "action:act_91966280c188:v3"`
- `Execution Attempts: 0 (verified)`
- `Execution Receipts: 0 (verified)`
- `Mode: --dry-run (default). No HTTP execute request was sent.`

### Step 2: Open Dedicated Stage C Dispatch Window
Start container with dedicated override (enables sending flags strictly for this instance):
```bash
docker compose -f docker-compose.yml -f docker-compose.stage-c-dispatch.yml up -d caseworker-api
```

Verify TLS readiness:
```bash
curl --cacert certs/localhost.crt https://localhost:3443/health/ready
```

### Step 3: Single-Shot Authorized Dispatch
Execute transmission with explicit authorization flag and deliberate operator decision token:
```bash
python3 scripts/guarded_stage_c_dispatch.py \
  --execute-authorized \
  --operator-decision-token="CONFIRM_STAGE_C_SEND_act_91966280c188"
```

Expected Four-Tier Output:
- **Tier 1**: `Outcome Status: CONFIRMED_COMPLETION`, `HTTP Status Code: 200`
- **Tier 2**: `Google Message ID: <google-id>`, `Provider Acceptance: CONFIRMED`
- **Tier 3**: `Receipt ID: rcpt-...`, `Receipt Status: verified_success`, `Receipt Hash: <sha256>`
- **Tier 4**: `Target Mailbox: dagogograntjack@gmail.com`

### Step 4: Scoped Disarm (Automatic or Manual)
The dispatcher automatically performs scoped container recreation upon confirmed completion:
`docker compose up -d --force-recreate caseworker-api`

Confirm flags are disarmed (returns `false false`):
```bash
docker compose exec caseworker-api python3 -c '
import os
print(os.environ.get("CASEWORKER_REAL_EXECUTION_ENABLED"), os.environ.get("CASEWORKER_GMAIL_EXECUTION_ENABLED"))
'
```

### Step 5: Verification & Delivery Confirmation
1. Verify database recorded exactly 1 attempt and 1 receipt:
```bash
docker compose exec caseworker-api python3 -c '
import sqlite3
conn = sqlite3.connect("/app/.caseworker/caseworker.db")
c = conn.cursor()
c.execute("SELECT count(*) FROM execution_attempts;")
print("Attempts:", c.fetchone()[0])
c.execute("SELECT count(*) FROM execution_receipts;")
print("Receipts:", c.fetchone()[0])
'
```
2. Verify Gmail recipient mailbox (`dagogograntjack@gmail.com`):
   - Subject: `[JACKVERSE INTERNAL TEST] Governed Gmail Transmission Verification`
   - Attachment: `cv_operator-1.txt` (131 bytes, SHA-256: `43057a3551057a31e6cc00494ce435943b2a1e25f4f61a23a253ac58326a3573`)
