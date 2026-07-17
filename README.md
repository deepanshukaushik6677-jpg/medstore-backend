# Medical Store SaaS — Backend (Phase 1-6)

FastAPI + PostgreSQL. Phase 1: auth, roles, multi-tenancy. Phase 2: live
inventory ledger + purchase entries. Phase 3: Fast Billing / Counter Mode.
Phase 4: Expiry Protection + Smart Reorder Assistant. Phase 5: Analytics
Dashboard. Phase 6: a few small additions the frontend needed, described
below (the frontend itself lives in the separate `medstore-frontend`
project). All phases have been run end-to-end against a real local
Postgres instance before handoff.

## What's in Phase 1

- Full schema for every module in the brief (models/*.py) — inventory,
  purchasing, billing, customer credit — so later phases don't need schema
  changes, just new endpoints.
- Auth: phone+password login for Owner/Staff, email+password for Admin,
  JWT access + refresh tokens.
- Role enforcement (`require_roles`) and tenant scoping (`get_tenant_store_id`)
  in `app/dependencies.py` — this is the piece every endpoint in the build
  goes through.
- Admin: create a store + its Owner in one call, list stores (status only,
  never store data), and grant time-boxed logged access to a store.
- Owner: create/list/deactivate/reset-password for their own Staff.

## What's in Phase 2

- **Medicines** (`/medicines`): create/list/get/update. Either Owner or Staff
  can add a medicine (Staff need this mid-receiving for something never
  stocked before); only Owner can set `hsn_code`/`gst_rate`. Staff can only
  edit `barcode`/`zone`/`rack`/`shelf`/`box` on an existing medicine —
  matches the Locator brief. Every response computes `total_stock` from live
  batches; `purchase_cost` on a batch is stripped from Staff-facing responses
  regardless of what's stored.
- **Suppliers** (`/suppliers`): Owner manages them; Staff has read access for
  the stock-in dropdown.
- **Purchase entries** (`/purchase-entries`): `POST` is Staff's stock-in
  receiving screen — supplier + line items (medicine, batch number, expiry,
  quantity, MRP), no cost fields. Batches go **live immediately** on
  submission — stock accuracy never waits on bookkeeping. `PATCH
  .../complete-financials` is Owner-only and fills in invoice/amount/due
  date/payment status plus per-line unit cost, which retroactively updates
  each batch's `purchase_cost` (margin queries join live batch cost rather
  than a frozen snapshot, so this corrects any sale already made from that
  batch). Every response masks `unit_cost` and all purchase-ledger financial
  fields to `None` unless the caller is Owner.

## What's in Phase 3

- **Store settings** (`/owner/store-settings`, Owner-only) — added because
  billing can't be tested without a way to set GSTIN / default bill type /
  GST numbering mode. `GET` and `PATCH`.
- **Bill preview** (`POST /bills/preview`) — read-only, computes the cart
  total (FEFO-resolved, GST-aware) without touching stock. This is what
  powers "watch the cart total grow" without committing a sale on every
  scan.
- **Checkout** (`POST /bills`) — the one mutating action. For each cart
  line, walks that medicine's batches in expiry order and draws from as
  many as needed (splitting across batches if the nearest-expiry one
  doesn't have enough) — Staff never chooses a batch. Decrements
  `quantity_on_hand` under row locks, so concurrent checkouts at a busy
  counter can't oversell the same batch.
- **Idempotent by design**: every request carries a client-generated
  `idempotency_key`; retrying the same key (e.g. an offline queue flushing
  after a dropped connection) returns the original bill instead of
  double-selling. Verified: same key twice → same bill, stock deducted once.
- **Sequential invoice numbering**: GST and Simple bills are numbered in
  separate series (`InvoiceCounter`, keyed by store + series + financial
  year) via an atomic `UPDATE ... RETURNING`, so a Simple sale never
  introduces a gap in the legally-sensitive GST sequence. Honors the
  confirmed `gst_numbering_mode` (fy_reset / continuous) per store.
- **GST compliance gates, enforced server-side, not just documented**: a GST
  bill is rejected with a clear message if the store has no GSTIN set, or if
  any cart medicine is missing its GST rate or HSN code.
- **Undo/edit window** (`PATCH /bills/{id}/void`) — reverses the stock
  deduction and marks the bill voided, but only within 5 minutes of
  completion (the brief's "a few minutes' grace," made concrete). Open to
  any Owner/Staff at that store, not just whoever rang it up.
- `payment_mode: "credit"` (Udhaar) is explicitly rejected for now with a
  clear message — the ledger side of customer credit is still deferred, so
  a bill shouldn't silently pretend to support it.

## What's in Phase 4

- **Medicine Locator**: nothing new to build — `GET /medicines?search=` and
  `?barcode=` (Phase 2) already cover name/barcode lookup with location
  fields, and Staff could already edit `zone`/`rack`/`shelf`/`box` from
  Phase 2's `PATCH /medicines/{id}`. Camera scanning itself is a frontend
  concern (a library like `html5-qrcode`), not a backend one.
- **Expiry Protection** (`GET /medicines/expiry-alerts`, Owner + Staff — it's
  explicitly part of the Today dashboard for both roles): flags every
  in-stock batch that's either already expired or inside its *specific
  supplier's* return window — found by joining the batch back to the
  purchase entry that created it. A batch with no traceable supplier falls
  back to a 0-day window rather than guessing.
- **Smart Reorder Assistant** (`GET /medicines/reorder-suggestions`,
  Owner-only — this is a purchasing/strategy call, not a counter task):
  flags medicines at or below their `reorder_threshold`, estimates weekly
  sales velocity from the last 30 days of completed bills, and suggests a
  reorder quantity (covers ~2 weeks of velocity, or clears the threshold
  shortfall, whichever is bigger) — grouped by whichever supplier most
  recently supplied that medicine, so the output reads "Supplier X: reorder
  these N items."
- **A real bug fixed along the way**: building Expiry Protection surfaced
  that Fast Billing's FEFO query never excluded already-expired batches —
  it would have happily sold expired stock if the nearest-expiry batch
  had passed its date. Fixed in `app/routers/billing.py` (`_resolve_cart`
  now filters `expiry_date >= today`), and the Reorder Assistant's stock
  count was corrected the same way, so "how much can I actually sell" and
  "do I need to reorder" both ignore stock that's already expired.

## What's in Phase 5

All under `/analytics`, all Owner-only per the brief:

- **`GET /analytics/overview?period=day|week|month`** — order count, pre-tax
  subtotal, GST collected, total collected, and gross profit/margin for that
  period. Profit is computed against `subtotal` (not the GST-inclusive
  total), since tax collected on the government's behalf isn't revenue.
  Boundaries are computed in IST and converted to UTC for the query — this
  app is India-only per the brief, and India has one fixed offset
  year-round, so a plain fixed offset is correct here (a multi-country
  product would need a real per-store timezone).
- **`GET /analytics/trend?granularity=day|week|month&count=N`** — a time
  series of the same figures, for charting the "daily/weekly/monthly views"
  the brief asks for.
- **`GET /analytics/top-sellers?period=...&limit=N`** — medicines ranked by
  quantity sold in the period.
- **`GET /analytics/slow-movers?days=N&limit=N`** — in-stock medicines
  ranked by *least* sold over the lookback window — the dead-stock
  candidates the brief says should feed into not over-ordering them again.
  Already-expired stock is excluded (that's Expiry Protection's job, not a
  reordering signal).
- **Honest about incomplete data**: any sale made from a batch whose
  purchase cost hasn't been entered yet (Owner hasn't completed that
  purchase entry's financials — see Phase 2) is excluded from the profit
  figure and counted separately in `lines_pending_cost`, rather than being
  silently treated as zero-cost and overstating margin. Verified live: profit
  read as understated while a purchase entry was still pending, then jumped
  to the exact correct value the moment financials were completed — no
  re-computation needed, since margin queries join the batch's current cost
  rather than a frozen snapshot.
- Voided bills are excluded everywhere, verified against real data.

## What's in Phase 6 (backend side)

Two small additions the frontend needed, plus a refactor:

- **`GET /dashboard/today`** (Owner + Staff) — one call for the whole Today
  home screen: sales total, order count, low-stock count, expiring-soon
  count, and (Owner only — purchase-ledger financial detail, masked to
  `null` for Staff, same rule as everywhere else) pending supplier dues.
  Reuses the exact IST day boundary from Analytics and the exact expiry/
  low-stock logic from Phase 4, so all three surfaces always agree.
- **`PATCH /auth/me/tour-completed`** (Owner + Staff) — flips the account's
  `tour_completed` flag once the first-login coach-mark tour finishes or is
  skipped, so it's tied to the account (works across devices) rather than
  a local flag that forgets the moment someone clears their browser.
- **`/auth/me` now also returns `default_bill_type` and `gstin_set`** —
  added once the frontend's offline billing queue needed a way for Staff to
  resolve "use the store's default bill type" locally when there's no
  server to ask. Both ride along on the same call the frontend already
  makes right after login.
- **Admin endpoints rounded out** for the frontend's Admin app:
  `GET /admin/stores/{id}` (single store), `PATCH /admin/stores/{id}`
  (the one lifecycle action Admin has — active/suspended), and
  `GET /admin/stores/{id}/access-grants` (audit history of every grant,
  not just creating new ones). Same access boundary as everywhere else:
  no store inventory/sales/customer data in any of these responses.
- **Refactor**: `IST`, `period_bounds`, `sellable_stock`, and the expiry-
  alert query moved into `app/utils.py` so Analytics, Insights, and the new
  Dashboard endpoint share one definition of "today," "in stock," and
  "expiring soon" instead of three copies that could drift apart. No
  behavior changed — re-verified against the full Phase 1-5 test suite of
  manual checks after the move, zero regressions.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env if your Postgres isn't on localhost:5432 with these creds

# create the db (adjust to your local Postgres setup)
psql -c "CREATE USER medstore WITH PASSWORD 'medstore';"
psql -c "CREATE DATABASE medstore OWNER medstore;"

alembic upgrade head

# bootstrap the first platform admin — there's no public signup for this
python -m app.seed "Your Name" "admin@yourdomain.com" "a-strong-password"

uvicorn app.main:app --reload
```

API docs at `http://localhost:8000/docs` once it's running.

## Design decisions baked in from the brief (see inline comments for detail)

- **Stock-in / purchase financials split**: one `PurchaseEntry` record, but
  `invoice_no`/`amount`/`due_date`/`payment_status` and each line's
  `unit_cost` are nullable and populated by an Owner later — Staff creates
  the record with batch fields only. See `app/models/purchasing.py` and
  `app/routers/purchasing.py`.
- **GST invoice numbering**: `Store.gst_numbering_mode` is owner-configurable
  (`fy_reset` or `continuous`); `InvoiceCounter` (keyed by store + series +
  fy_year) backs the actual sequence and is only ever touched server-side,
  under an atomic update, at bill completion.
- **Drug license number**: optional field on `Store`, no activation gate.
- **Admin access to store data**: never ambient. `AdminAccessLog` is the only
  path in; `get_admin_scoped_store_id` in `dependencies.py` enforces it.
- **CGST/SGST split**: computed as half of the total GST amount at response
  time (intra-state assumption — reasonable for a single local store; no new
  stored columns needed since it's a pure display derivation).
- **Reorder math isn't in the brief** (it only says "flag low stock" and
  "suggest quantities by supplier") — `VELOCITY_LOOKBACK_DAYS = 30` and
  `REORDER_COVER_WEEKS = 2` in `app/routers/insights.py` are explainable
  defaults, not requirements; easy to change in one place.
- **`/bills?bill_date=` (Phase 3) vs `/analytics/overview?period=day` /
  `/dashboard/today` (Phase 5-6) use different day boundaries on purpose**:
  the former is a simple UTC-calendar-day filter for pulling up bills to
  review; the latter two are the IST-correct "today" figure meant for the
  actual sales KPI. Worth knowing so it isn't mistaken for an inconsistency
  later.

## Remaining work

The backend now covers every module in the brief. What's left is entirely
frontend (see `medstore-frontend/README.md` for the detailed breakdown):
the offline billing queue, the Owner-only screens (Inventory, Purchase
Ledger, Expiry Protection, Reorder Assistant, Analytics, Store Settings,
Staff management), camera barcode scanning, and a fuller icon-first nav
once those screens exist.

