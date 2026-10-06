-- stock-analyzer schema — LOCKED in R0 on 2026-10-06. PILOT-OWNED.
-- Workers must not alter table or column definitions. Need a change?
-- write it to status/<Wn>.md as a blocker; the pilot decides.
--
-- Conventions that apply to EVERY table:
--   * date / buy_date / *_at are ISO 'YYYY-MM-DD' (or ISO8601 for timestamps).
--     TWSE/TPEx return ROC dates ("1151005"); convert at the boundary.
--   * A missing number is NULL, never '' and never 0. Sources return ''.
--   * code keeps the source's own spelling, including letter suffixes
--     ('0050', '00679B', '00400A', '2330'). Never zero-strip or upper/lower it.

CREATE TABLE IF NOT EXISTS instrument (
    code        TEXT PRIMARY KEY,
    name        TEXT,
    board       TEXT    NOT NULL,            -- 'TWSE' (上市) | 'TPEX' (上櫃)
    kind        TEXT    NOT NULL,            -- 'ETF' | 'STOCK'
    yf_symbol   TEXT    NOT NULL,            -- code + '.TW' if TWSE else '.TWO'
    in_universe INTEGER NOT NULL DEFAULT 0,  -- 1 = backfill + screen this one
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS price_daily (
    code      TEXT    NOT NULL,
    date      TEXT    NOT NULL,              -- ISO 'YYYY-MM-DD'
    board     TEXT    NOT NULL,              -- 'TWSE' | 'TPEX'
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    adj_close REAL,                          -- yfinance only; NULL on TWSE/TPEx rows
    volume    INTEGER,                       -- 股數 (shares, not 張)
    turnover  REAL,                          -- 成交金額; NULL on yfinance rows
    source    TEXT    NOT NULL,              -- 'twse' | 'tpex' | 'yfinance'
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_price_date ON price_daily (date);
CREATE INDEX IF NOT EXISTS idx_price_code ON price_daily (code);

CREATE TABLE IF NOT EXISTS fundamental (
    code           TEXT NOT NULL,
    date           TEXT NOT NULL,
    pe_ratio       REAL,
    dividend_yield REAL,                     -- percent, as served (3.20 = 3.20%)
    pb_ratio       REAL,
    source         TEXT NOT NULL,            -- 'twse_bwibbu'
    PRIMARY KEY (code, date)
);

CREATE TABLE IF NOT EXISTS news (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    published_at TEXT NOT NULL,              -- ISO8601
    title        TEXT NOT NULL,
    link         TEXT NOT NULL,              -- Google redirect URL; see facts.md
    publisher    TEXT,
    query        TEXT,                       -- the search term used
    fetched_at   TEXT NOT NULL,
    UNIQUE (title, published_at)
);

-- Added by the pilot in R3, after all workers finished. Index data cannot live
-- in price_daily: board allows only TWSE/TPEX and instrument.kind only
-- ETF/STOCK, so '^TWII' would violate both conventions.
CREATE TABLE IF NOT EXISTS index_daily (
    symbol TEXT NOT NULL,                    -- '^TWII'
    date   TEXT NOT NULL,                    -- ISO 'YYYY-MM-DD'
    open   REAL,
    high   REAL,
    low    REAL,
    close  REAL,
    source TEXT NOT NULL,                    -- 'yfinance' | 'twse_mi_index'
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS holdings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT NOT NULL,
    buy_date   TEXT NOT NULL,                -- ISO 'YYYY-MM-DD'
    shares     REAL NOT NULL,
    cost_price REAL NOT NULL,
    note       TEXT,
    UNIQUE (code, buy_date, cost_price)
);
