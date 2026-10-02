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
