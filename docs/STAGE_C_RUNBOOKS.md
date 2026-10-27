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
Stop caseworker-api to prevent concurrent mutations:
```bash
docker compose stop caseworker-api
```

### Step 2: Create Consistent Pre-Migration Online Backup
Execute backup via SQLite Online Backup API (safely checkpoints WAL):
```bash
docker compose run --rm caseworker-api python3 scripts/rotate_oauth_encryption_key.py \
  --db-path /app/.caseworker/caseworker.db \
  --env-file .env.caseworker-api.local \
  --backup-dir /app/.caseworker/backups \
  --backup-only
```

### Step 3: Execute Migration
Re-encrypt OAuth records and update `.env.caseworker-api.local`:
```bash
docker compose run --rm caseworker-api python3 scripts/rotate_oauth_encryption_key.py \
  --db-path /app/.caseworker/caseworker.db \
  --env-file .env.caseworker-api.local \
  --backup-dir /app/.caseworker/backups \
  --migrate
```

### Step 4: Verification
Audit encryption status and confirm all records decrypt:
```bash
docker compose run --rm caseworker-api python3 scripts/rotate_oauth_encryption_key.py \
  --db-path /app/.caseworker/caseworker.db \
  --env-file .env.caseworker-api.local \
  --check
```

### Step 5: Resume Service
Start `caseworker-api` and verify TLS readiness:
```bash
docker compose up -d caseworker-api
curl --cacert certs/localhost.crt https://localhost:3443/health/ready
```

### Rollback Procedure (If Interrupted or Failed)
If the migration was interrupted:
```bash
# Check if crash recovery can auto-resume:
docker compose run --rm caseworker-api python3 scripts/rotate_oauth_encryption_key.py \
  --db-path /app/.caseworker/caseworker.db \
  --env-file .env.caseworker-api.local \
  --resume

# Or restore database from verified backup:
docker compose run --rm caseworker-api python3 scripts/rotate_oauth_encryption_key.py \
  --db-path /app/.caseworker/caseworker.db \
  --restore-backup /app/.caseworker/backups/caseworker_backup_<timestamp>.db
```

---

## 2. One-Time Governed Gmail Dispatch Runbook

### Purpose
Execute the single authorized internal test transmission (`act_91966280c188`) to `dagogograntjack@gmail.com` using dedicated Compose override barriers, trusted TLS verification, and immediate disarm.

> [!WARNING]
> **No Blind Retries**: If the execute request times out in flight, do **not** re-run the dispatch command. Check `execution_attempts` and `execution_receipts` first. A timed-out request may have already reached Gmail.

### Pre-conditions Checklist
- [ ] Credential migration completed and verified (`--check` passed).
- [ ] Zero execution attempts in database (`SELECT count(*) FROM execution_attempts` == 0).
- [ ] Zero execution receipts in database (`SELECT count(*) FROM execution_receipts` == 0).
- [ ] Human approval `65cb25bb-0539-4f61-917c-46cf4a84f9dd` intact.
- [ ] Plan fingerprint matches `6e46f1a5cef7364a843c4b8e8713fa05a0506673a41815597b55107fa729a0ae`.
- [ ] Both execution flags in base deployment are confirmed `false`.

### Step 1: Preflight Audit (Dry Run)
Verify governance chain-of-custody over trusted TLS without transmitting:
```bash
python3 scripts/guarded_stage_c_dispatch.py --dry-run
```
Expected output:
- `[PASS]` Trusted TLS connection to `https://localhost:3443`
- `[PASS]` Signed operator bearer token generated
- `Action ID: act_91966280c188 (Status: approved, Version: 3)`
- `ETag Header: "action:act_91966280c188:v3"`
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
Execute transmission with explicit authorization flag:
```bash
python3 scripts/guarded_stage_c_dispatch.py --execute-authorized --non-interactive-approved
```

Expected output:
- `HTTP Status: 200`
- `Receipt ID: rcpt-...`
- `Receipt Status: verified_success`
- `Provider Message ID: ...`
- `Case Status: waiting_external`

### Step 4: Immediate Disarm (Mandatory)
Immediately close the dispatch window by restoring standard configuration:
```bash
docker compose -f docker-compose.yml -f docker-compose.stage-c-dispatch.yml down
docker compose up -d
```

Verify flags are disarmed (returns `false`):
```bash
docker compose exec caseworker-api python3 -c '
import os
print("REAL_EXECUTION:", os.environ.get("CASEWORKER_REAL_EXECUTION_ENABLED"))
print("GMAIL_EXECUTION:", os.environ.get("CASEWORKER_GMAIL_EXECUTION_ENABLED"))
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
