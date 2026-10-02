-- Wallet schema v1 (owner: Timmy). Money is integer HKD cents; times are
-- RFC 3339 strings normalised to +08:00 with second precision, so string
-- comparison orders them correctly (Hong Kong has no DST).
-- audit_events belongs to Seungbin's audit module and is not created here.

CREATE TABLE IF NOT EXISTS schema_version (
    component TEXT PRIMARY KEY,
    version   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS mandates (
    id                TEXT PRIMARY KEY,
    owner_id          TEXT NOT NULL,
    delegatee_id      TEXT NOT NULL,
    parent_mandate_id TEXT REFERENCES mandates(id),
    draft_id          TEXT NOT NULL UNIQUE,
    version           INTEGER NOT NULL CHECK (version >= 1),
    status            TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
    policy_json       TEXT NOT NULL,
    expires_at        TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    revoked_at        TEXT
);
CREATE INDEX IF NOT EXISTS mandates_parent ON mandates(parent_mandate_id);

-- Immutable once written. body_json is the full contract Quote.
CREATE TABLE IF NOT EXISTS quotes (
    id            TEXT PRIMARY KEY,
    family_id     TEXT NOT NULL,  -- owning user; agents share their owner's family
    created_by    TEXT NOT NULL,
    merchant_id   TEXT NOT NULL,
    revision      TEXT NOT NULL,
    total_minor   INTEGER NOT NULL CHECK (total_minor >= 0),
    basket_hash   TEXT NOT NULL,
    request_json  TEXT NOT NULL,  -- items + delivery context, for re-pricing at payment
    body_json     TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS budget_periods (
    id             TEXT PRIMARY KEY,
    mandate_id     TEXT NOT NULL REFERENCES mandates(id),
    period         TEXT NOT NULL CHECK (period IN ('calendar_week', 'calendar_month')),
    starts_at      TEXT NOT NULL,
    ends_at        TEXT NOT NULL,
    limit_minor    INTEGER NOT NULL CHECK (limit_minor >= 1),
    paid_minor     INTEGER NOT NULL DEFAULT 0 CHECK (paid_minor >= 0),
    reserved_minor INTEGER NOT NULL DEFAULT 0 CHECK (reserved_minor >= 0),
    version        INTEGER NOT NULL DEFAULT 0,
    CHECK (paid_minor + reserved_minor <= limit_minor),
    UNIQUE (mandate_id, period, starts_at)
);

CREATE TABLE IF NOT EXISTS reservations (
    id              TEXT PRIMARY KEY,
    transaction_id  TEXT NOT NULL UNIQUE,
    mandate_id      TEXT NOT NULL REFERENCES mandates(id),
    mandate_version INTEGER NOT NULL,
    quote_id        TEXT NOT NULL REFERENCES quotes(id),
    basket_hash     TEXT NOT NULL,
    merchant_id     TEXT NOT NULL,
    amount_minor    INTEGER NOT NULL CHECK (amount_minor >= 0),
    status          TEXT NOT NULL CHECK (status IN ('reserved', 'paid', 'cancelled', 'expired')),
    token_id        TEXT NOT NULL UNIQUE,
    issued_at       TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    closed_at       TEXT
);
CREATE INDEX IF NOT EXISTS reservations_open ON reservations(status, expires_at);

CREATE TABLE IF NOT EXISTS reservation_periods (
    reservation_id TEXT NOT NULL REFERENCES reservations(id),
    period_id      TEXT NOT NULL REFERENCES budget_periods(id),
    PRIMARY KEY (reservation_id, period_id)
);

-- One authorization decision per transaction_id, whatever idempotency key
-- was used. response_json never contains the signed token.
CREATE TABLE IF NOT EXISTS auth_decisions (
    transaction_id TEXT PRIMARY KEY,
    decision_id    TEXT NOT NULL UNIQUE,
    actor_id       TEXT NOT NULL,
    mandate_id     TEXT NOT NULL,
    quote_id       TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('approved', 'refused', 'requires_review')),
    reservation_id TEXT REFERENCES reservations(id),
    response_json  TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    receipt_id     TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL UNIQUE,
    reservation_id TEXT NOT NULL UNIQUE REFERENCES reservations(id),
    decision_id    TEXT NOT NULL,
    event_sequence INTEGER NOT NULL,
    receipt_json   TEXT NOT NULL,
    paid_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    actor_id      TEXT NOT NULL,
    operation     TEXT NOT NULL,
    key           TEXT NOT NULL,
    request_hash  TEXT NOT NULL,
    status_code   INTEGER NOT NULL,
    response_json TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (actor_id, operation, key)
);

-- Simulated rails (mandate/payments/rails.py). Not a real issuer or bank: no
-- money moves. One rail account per (rail, mandate); one single-use
-- credential per reservation, locked to merchant, amount, expiry and token.
CREATE TABLE IF NOT EXISTS rail_accounts (
    rail        TEXT NOT NULL,
    mandate_id  TEXT NOT NULL REFERENCES mandates(id),
    ref         TEXT NOT NULL UNIQUE,
    limit_minor INTEGER NOT NULL CHECK (limit_minor >= 1),
    currency    TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('active', 'closed')),
    issued_at   TEXT NOT NULL,
    closed_at   TEXT,
    PRIMARY KEY (rail, mandate_id)
);

CREATE TABLE IF NOT EXISTS rail_payments (
    reservation_id TEXT PRIMARY KEY REFERENCES reservations(id),
    rail           TEXT NOT NULL,
    account_ref    TEXT NOT NULL REFERENCES rail_accounts(ref),
    credential_id  TEXT NOT NULL UNIQUE,
    last4          TEXT,
    merchant_id    TEXT NOT NULL,
    amount_minor   INTEGER NOT NULL CHECK (amount_minor >= 0),
    currency       TEXT NOT NULL,
    purpose        TEXT NOT NULL,
    token_id       TEXT NOT NULL UNIQUE,
    expires_at     TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('held', 'captured', 'voided', 'refunded')),
    captured_minor INTEGER NOT NULL DEFAULT 0 CHECK (captured_minor <= amount_minor),
    refunded_minor INTEGER NOT NULL DEFAULT 0 CHECK (refunded_minor <= captured_minor),
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

-- Which payment route a reservation uses, with the ranking it was chosen from.
CREATE TABLE IF NOT EXISTS reservation_routes (
    reservation_id TEXT PRIMARY KEY REFERENCES reservations(id),
    route_id       TEXT NOT NULL,
    rail           TEXT NOT NULL,
    choice_json    TEXT NOT NULL
);

-- Spend and reward per route, for tiered caps ("4% on the first HK$10,000 a
-- month") and for unwinding a reward when its payment is refunded.
CREATE TABLE IF NOT EXISTS reward_ledger (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id       TEXT NOT NULL,
    route_id       TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('earn', 'reverse')),
    spend_minor    INTEGER NOT NULL,
    reward_minor   INTEGER NOT NULL,
    period_start   TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    UNIQUE (transaction_id, kind)
);
CREATE INDEX IF NOT EXISTS reward_ledger_period ON reward_ledger(owner_id, route_id, period_start);

CREATE TABLE IF NOT EXISTS refunds (
    id                    TEXT PRIMARY KEY,
    transaction_id        TEXT NOT NULL UNIQUE REFERENCES payments(transaction_id),
    amount_minor          INTEGER NOT NULL CHECK (amount_minor >= 0),
    reward_reversed_minor INTEGER NOT NULL CHECK (reward_reversed_minor >= 0),
    route_id              TEXT,
    rail_ref              TEXT,
    reason                TEXT,
    refunded_by           TEXT NOT NULL,
    refunded_at           TEXT NOT NULL,
    response_json         TEXT NOT NULL
);

-- A person's answer to a purchase that needs review. Pending requests lapse
-- at expires_at; an approval is a one-time grant for that exact transaction,
-- quote and mandate version.
CREATE TABLE IF NOT EXISTS approval_requests (
    id              TEXT PRIMARY KEY,
    transaction_id  TEXT NOT NULL UNIQUE REFERENCES auth_decisions(transaction_id),
    owner_id        TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    mandate_id      TEXT NOT NULL REFERENCES mandates(id),
    mandate_version INTEGER NOT NULL,
    quote_id        TEXT NOT NULL REFERENCES quotes(id),
    merchant_id     TEXT NOT NULL,
    basket_hash     TEXT NOT NULL,
    amount_minor    INTEGER NOT NULL,
    reasons_json    TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'denied', 'expired', 'used')),
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    decided_at      TEXT,
    decided_by      TEXT,
    note            TEXT
);
CREATE INDEX IF NOT EXISTS approval_requests_open ON approval_requests(status, expires_at);
