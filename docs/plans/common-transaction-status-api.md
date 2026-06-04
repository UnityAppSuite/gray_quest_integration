# GrayQuest Common Transaction Status API — Integration + Auto-Reconcile Plan

> Target app: `apps/grayquest` (branch off `upstream/develop`)
> Intended saved location on implementation: `./features/grayquest-transaction-status-api/plan/PLAN.md`
> Status: Draft for approval

## 1. Context — why this change

GrayQuest provides a **Common Transaction Status API** (`GET /v1/orders/transaction-status`) to poll the status/progress of a payment or EMI application by `application_code`, `reference_id`, or `application_id` (see spec PDF). Today the grayquest app relies almost entirely on **webhooks** (`grayquest.api.webhook_handler`) to mark Payment Requests Paid, create Payment Entries, and sync EMI status. When a webhook is missed, delayed, or fails, a Payment Request can stay stuck in `Initiated`/`Requested` even though GrayQuest actually captured/disbursed the money.

**Finding:** the Common Transaction Status API is **NOT integrated** on `develop`. The closest existing method, `GrayQuestSettings.check_payment_status()`, hits a *different* endpoint (`/v1/payments/fetch`) and only returns raw JSON — it does not reconcile.

**Goal:** add a status-fetch method for the new endpoint and use its response to **auto-reconcile** the Payment Request (mark Paid / create Payment Entry / sync EMI status) as a safety-net for missed webhooks. Support all three identifiers per the spec.

## 2. What already exists and will be reused

All in `apps/grayquest` on `upstream/develop`:

- `grayquest/grayquest/doctype/grayquest_settings/grayquest_settings.py`
  - `get_headers()` — builds `Authorization: Basic base64(client_id:client_secret)` + `GQ-API-Key` headers (exactly what the new API needs).
  - `make_request(method, endpoint, payload, params, reference_doctype, reference_docname, request_description)` — does `requests.get/post`, **logs every call via `create_request_log` → Integration Request** with `handle_success`/`handle_failure`, commits, returns the `Response`. **Reuse as-is.**
  - `check_payment_status(payment_request)` — the style template for the new fetch method.
  - `handle_webhook(data)` — routes `entity="direct"` → `handle_payment_gateway_webhook`, `"monthly-emi"` → `handle_emi_webhook`.
- `grayquest/utils/webhook.py`
  - `resolve_payment_request(udf_details)` → `(doctype, docname, fee_type)` from `udf_1`/`udf_2` (+ fallback). The status response carries the **same `udf_details` convention** our order payload sends, so this resolves the PR from a status response.
  - `handle_payment_gateway_webhook(data)` (`dt.payment.captured`) and `handle_emi_webhook(data)` (`emi.disbursed`) — both already carry an **"already Paid" idempotency guard**, set Mode of Payment, set `frappe.flags.webhook_posting_date`, and call `doc.on_payment_authorized(status="Completed")`.
  - `update_emi_status(doc, event, timestamp)`, `add_webhook_log(data)`, `create_payment_entry(...)`, `_parse_webhook_date(...)`.
- `grayquest/utils/__init__.py` — `EMI_STATUS_MAPPING` (event → `GrayQuest EMI Status` child label).
- `grayquest/api.py` — thin `@frappe.whitelist()` wrappers calling controller methods.

**No new DocType and no schema change** — reuse existing `transaction_id`, `emi_status` table, and `GrayQuest EMI Status` child Select options. Single `api_url` field stays (admin sets stage vs live manually, per decision).

## 3. Design — recommended approach

The transaction-status response is **state-shaped** (`data.logs[].attempts[].status`), while the webhook handlers are **event-shaped** (`data["event"]`). Rather than duplicate the booking logic, **translate the status response into a minimal synthetic webhook-shaped dict and call the existing handlers.**

Rationale: zero duplication of the battle-tested booking path; the handlers' existing **"already Paid" guard makes reconcile idempotent** (safe to run repeatedly / after a webhook already processed); the status payload contains everything the synthetic dict needs (`udf_details`, amounts, `payment_id`, `created_at`).

### 3.1 Status-string mapping (driven from a dict, not hardcoded branches)

Add to `grayquest/utils/__init__.py`:

```python
# attempts[].status (transaction-status API) -> normalized outcome
TXN_STATUS_OUTCOME = {
    "PAID": "success", "SUCCESS": "success", "DISBURSED": "success",
    "CAPTURED": "success", "COMPLETED": "success",
    "PENDING": "pending", "INITIATED": "pending", "IN_PROCESS": "pending", "PROCESSING": "pending",
    "FAILED": "failed", "REJECTED": "failed", "CANCELLED": "failed", "EXPIRED": "failed",
}
```
- Match case-insensitively (`status.upper()`). `success` → reconcile; `pending`/`failed`/unknown → **no-op + log** (never book). Unknown strings logged so the vocabulary can be extended.
- For the EMI child table, reuse `EMI_STATUS_MAPPING` *labels* via a small attempt-status→label map (e.g. `PAID/DISBURSED → "Disbursed"`, `APPROVED → "Approved"`). Append through the existing `update_emi_status` path (already de-dupes).

### 3.2 New controller methods — `grayquest_settings.py`

```python
def get_transaction_status(self, application_code=None, reference_id=None, application_id=None,
                           reference_doctype=None, reference_docname=None):
    """GET /v1/orders/transaction-status. Mirrors check_payment_status.
       Build params with only provided keys; frappe.throw if all empty.
       Log via make_request (Integration Request, reference = PR when known). Return res.json()."""
```
Endpoint: `f"{self.api_url.strip('/')}/v1/orders/transaction-status"`.

```python
def reconcile_transaction(self, payment_request=None, application_code=None,
                          reference_id=None, application_id=None):
    """Safety-net auto-reconcile. Thin wrapper that delegates to
       webhook.reconcile_from_status(data, payment_request)."""
```
Step 1: if `payment_request` given and no `application_code`, read its `transaction_id`.
Step 2: `data = self.get_transaction_status(...)`.
Step 3: delegate to `reconcile_from_status`.

### 3.3 Orchestration + adapters — `grayquest/utils/webhook.py`

```python
def _status_attempt_amount(attempt):           # sanctioned_amount or applied_amount
def _status_to_pg_webhook(data_block, attempt, application_code):   # -> dt.payment.captured shape
def _status_to_emi_webhook(data_block, attempt, application_code):  # -> emi.disbursed shape
def reconcile_from_status(data, payment_request=None):             # orchestration
```

`reconcile_from_status`:
1. If `not data.get("success")` → `frappe.log_error` + return `{"status":"error","reconciled":False}`.
2. Resolve PR: passed `payment_request` first; else `resolve_payment_request(data["data"]["udf_details"])`. If none exists → log full `udf_details`+identifiers, return `{"status":"unresolved","reconciled":False}`. **Never guess.**
3. For each `log` in `data["data"]["logs"]`: pick the **authoritative attempt** = latest `created_at` (tie-break highest `attempt_count`). Map status via `TXN_STATUS_OUTCOME`.
4. `payment_type == "EMI"` → EMI path; else PG path.
5. On `success`: build the synthetic payload and call `handle_emi_webhook` / `handle_payment_gateway_webhook` (idempotent via "already Paid" guard).
6. Always sync the `emi_status` child table from `logs` (even when pending).
7. Return `{"status","reconciled":bool,"payment_request","outcome","message"}`.

### 3.4 Synthetic payload mapping (the load-bearing translation)

- **PG** (`handle_payment_gateway_webhook` reads): `udf_details` pass-through; `application_details.code` = resolved code; `payment_details.status` must be the literal `"PAID"`; `payment_details.amount` = `_status_attempt_amount`; `payment_details.paid_on` = `attempt["created_at"]` (`_parse_webhook_date` already parses `DD-MM-YYYY HH:MM:SS`); omit `bank_reference_id`.
- **EMI** (`handle_emi_webhook` reads): `event="emi.disbursed"`, `entity="monthly-emi"`; `disbursement_details.disbursed_amount` = `_status_attempt_amount`; `disbursement_details.date` = `created_at`; `application_details.code` = code; `udf_details` pass-through; `timestamp` = `created_at`; omit `utr` if absent.

### 3.5 Whitelisted wrappers — `grayquest/api.py`

```python
@frappe.whitelist()
def get_transaction_status(application_code=None, reference_id=None, application_id=None): ...

@frappe.whitelist(methods=["POST"])
def reconcile_transaction(payment_request=None, application_code=None,
                          reference_id=None, application_id=None): ...
```
Both authenticated (NOT `allow_guest`); reconcile is POST since it mutates. Each fetches `frappe.get_last_doc("GrayQuest Settings")` and sets `frappe.response["message"]`.

### 3.6 Optional (later phases, note only — don't over-build)

- **PR form button** "Check & Reconcile Status" in the GrayQuest PR client script → `frappe.call("grayquest.api.reconcile_transaction", {payment_request})` then `frm.reload_doc()`. Gate on `docstatus===1 && status!=="Paid"`.
- **Scheduler sweep**: hourly `scheduler_events` job over stale `Initiated` PRs with a non-empty `transaction_id`, chunked-enqueued, per-PR try/except, calling `reconcile_transaction`.

## 4. Error handling

- `success == false` → log + no booking.
- No PR resolvable → log identifiers + `udf_details`, return `unresolved`, never guess.
- Multiple/ambiguous attempts → pick latest `created_at`; log when >1 attempt exists.
- Multiple logs (PG + EMI) → process each against the resolved PR; "already Paid" guard prevents double-book.
- `success` attempt with `0`/`None` amount → do not book; log "reconciliation required".
- `make_request` already logs Integration Request success/failure and commits.

## 5. Files to create / modify

Modify:
- `grayquest/grayquest/doctype/grayquest_settings/grayquest_settings.py` — add `get_transaction_status()` + thin `reconcile_transaction()`.
- `grayquest/utils/webhook.py` — add `_status_attempt_amount`, `_status_to_pg_webhook`, `_status_to_emi_webhook`, `reconcile_from_status`.
- `grayquest/utils/__init__.py` — add `TXN_STATUS_OUTCOME` (+ attempt-status→EMI-label map) and export.
- `grayquest/api.py` — add the two whitelisted wrappers.

Optional later: GrayQuest PR client script (button); `hooks.py` `scheduler_events` (sweep).

No new DocType, no patch (unless a *new* `attempts[].status` value surfaces in real data → then add it to the `GrayQuest EMI Status` Select and migrate via `frappe.db.sql`, per team convention).

## 6. Phased task breakdown

1. **Mappings + adapters (pure, testable):** `TXN_STATUS_OUTCOME` + label map; `_status_attempt_amount`, `_status_to_pg_webhook`, `_status_to_emi_webhook`. Unit-test against the spec sample payload.
2. **Orchestration:** `reconcile_from_status()` — resolution, attempt selection, outcome mapping, EMI/PG dispatch, `emi_status` sync, error handling, idempotency check.
3. **Controller + API:** `get_transaction_status()` + `reconcile_transaction()` on the controller; two whitelisted wrappers. Smoke-test each identifier.
4. **(Optional) UI button** on Payment Request.
5. **(Optional) Scheduler sweep** over stale Initiated PRs.

## 7. Verification

- **Unit:** test the adapters + `TXN_STATUS_OUTCOME` against the PDF's sample EMI response (PENDING → no-op) and a synthesized PAID/DISBURSED response (→ success). Test `reconcile_from_status` with: resolvable PR + success (books once), already-Paid PR (no-op), unresolvable `udf_details` (returns `unresolved`), `success:false`.
- **Manual (bench console / `frappe.call`):**
  - `grayquest.api.get_transaction_status` with each of `application_code` / `reference_id` / `application_id` against the **stage** URL → confirm an Integration Request log row is created with redacted headers, and the response JSON returns.
  - On a real stage EMI/PG order: stop the webhook, call `grayquest.api.reconcile_transaction({payment_request})` → confirm PR moves to `Paid`, Payment Entry created, `emi_status` rows appended, and a second call is a clean no-op (idempotent).
- **Regression:** existing `webhook_handler` flow unchanged (handlers untouched; only called with synthetic data).

## 8. Conventions / housekeeping

- Branch off `upstream/develop` (fetch first); descriptive feature slug, no REQ id in branch name.
- No `Co-Authored-By` trailer on commits. Don't commit/push without explicit instruction.
- Drive any new status enum from the DocType Select, not hardcoded lists.
- Cleanup: remove the temporary edu_quality worktree created during exploration — `git -C apps/edu_quality worktree remove ../edu_quality-gaprod-wt` (path `/home/badal/Work/bench-v15/apps/edu_quality-gaprod-wt`).
