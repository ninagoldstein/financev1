import sqlite3
from datetime import datetime, timezone

from secpull.models import Company, DerivedFact, FinancialFact

_DDL = """
CREATE TABLE IF NOT EXISTS companies (
    cik        TEXT PRIMARY KEY,
    ticker     TEXT NOT NULL,
    name       TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS financials (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cik              TEXT NOT NULL REFERENCES companies(cik),
    metric           TEXT NOT NULL,
    tag_used         TEXT NOT NULL,
    value            REAL NOT NULL,
    unit             TEXT NOT NULL,
    fiscal_year      INTEGER NOT NULL,
    fiscal_period    TEXT NOT NULL,
    form             TEXT NOT NULL,
    end_date         TEXT NOT NULL,
    filed_date       TEXT NOT NULL,
    coverage_quality TEXT NOT NULL DEFAULT 'COMPLETE',
    UNIQUE (cik, metric, fiscal_year, fiscal_period, form, end_date)
);

CREATE TABLE IF NOT EXISTS derived_financials (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    cik                 TEXT NOT NULL REFERENCES companies(cik),
    metric              TEXT NOT NULL,
    source              TEXT NOT NULL DEFAULT 'derived',
    formula_used        TEXT NOT NULL,
    source_metrics_used TEXT NOT NULL,
    value               REAL,
    unit                TEXT NOT NULL,
    fiscal_year         INTEGER NOT NULL,
    fiscal_period       TEXT NOT NULL,
    form                TEXT NOT NULL,
    end_date            TEXT NOT NULL,
    coverage_flag       TEXT NOT NULL,
    coverage_quality    TEXT NOT NULL DEFAULT 'DERIVED',
    UNIQUE (cik, metric, fiscal_year, fiscal_period, form, end_date)
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add new columns to existing databases without dropping data."""
    fin_cols = {row[1] for row in conn.execute("PRAGMA table_info(financials)")}
    if "coverage_quality" not in fin_cols:
        conn.execute(
            "ALTER TABLE financials ADD COLUMN "
            "coverage_quality TEXT NOT NULL DEFAULT 'COMPLETE'"
        )
        conn.commit()

    has_derived = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='derived_financials'"
    ).fetchone()
    if has_derived:
        der_cols = {row[1] for row in conn.execute("PRAGMA table_info(derived_financials)")}
        if "coverage_quality" not in der_cols:
            conn.execute(
                "ALTER TABLE derived_financials ADD COLUMN "
                "coverage_quality TEXT NOT NULL DEFAULT 'DERIVED'"
            )
            conn.commit()


def upsert_company(conn: sqlite3.Connection, company: Company) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO companies (cik, ticker, name, fetched_at) VALUES (?, ?, ?, ?)",
        (company.cik, company.ticker, company.name,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def insert_facts(conn: sqlite3.Connection, facts: list[FinancialFact]) -> int:
    inserted = 0
    for f in facts:
        cur = conn.execute(
            """INSERT INTO financials
               (cik, metric, tag_used, value, unit, fiscal_year, fiscal_period,
                form, end_date, filed_date, coverage_quality)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (cik, metric, fiscal_year, fiscal_period, form, end_date)
               DO UPDATE SET tag_used         = excluded.tag_used,
                             value            = excluded.value,
                             unit             = excluded.unit,
                             filed_date       = excluded.filed_date,
                             coverage_quality = excluded.coverage_quality
               WHERE excluded.filed_date > financials.filed_date""",
            (f.cik, f.metric, f.tag_used, f.value, f.unit, f.fiscal_year,
             f.fiscal_period, f.form, f.end_date, f.filed_date,
             f.coverage_quality),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


def insert_derived_facts(conn: sqlite3.Connection, facts: list[DerivedFact]) -> int:
    inserted = 0
    for f in facts:
        cur = conn.execute(
            """INSERT INTO derived_financials
               (cik, metric, source, formula_used, source_metrics_used,
                value, unit, fiscal_year, fiscal_period, form, end_date,
                coverage_flag, coverage_quality)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (cik, metric, fiscal_year, fiscal_period, form, end_date)
               DO UPDATE SET formula_used        = excluded.formula_used,
                             source_metrics_used = excluded.source_metrics_used,
                             value               = excluded.value,
                             unit                = excluded.unit,
                             coverage_flag       = excluded.coverage_flag,
                             coverage_quality    = excluded.coverage_quality""",
            (f.cik, f.metric, f.source, f.formula_used, f.source_metrics_used,
             f.value, f.unit, f.fiscal_year, f.fiscal_period, f.form,
             f.end_date, f.coverage_flag, f.coverage_quality),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


def get_derived_facts(
    conn: sqlite3.Connection,
    cik: str,
    metric: str | None = None,
    coverage_flag: str | None = None,
) -> list[DerivedFact]:
    sql = """SELECT cik, metric, source, formula_used, source_metrics_used,
                    value, unit, fiscal_year, fiscal_period, form, end_date,
                    coverage_flag, coverage_quality
             FROM derived_financials WHERE cik = ?"""
    params: list = [cik]
    if metric is not None:
        sql += " AND metric = ?"
        params.append(metric)
    if coverage_flag is not None:
        sql += " AND coverage_flag = ?"
        params.append(coverage_flag)

    rows = conn.execute(sql, params).fetchall()
    return [
        DerivedFact(
            cik=r[0], metric=r[1], source=r[2], formula_used=r[3],
            source_metrics_used=r[4], value=r[5], unit=r[6],
            fiscal_year=r[7], fiscal_period=r[8], form=r[9],
            end_date=r[10], coverage_flag=r[11], coverage_quality=r[12],
        )
        for r in rows
    ]


def get_facts(
    conn: sqlite3.Connection,
    cik: str,
    metric: str | None = None,
) -> list[FinancialFact]:
    sql = """SELECT cik, metric, tag_used, value, unit, fiscal_year,
                    fiscal_period, form, end_date, filed_date, coverage_quality
             FROM financials WHERE cik = ?"""
    params: list = [cik]
    if metric is not None:
        sql += " AND metric = ?"
        params.append(metric)

    rows = conn.execute(sql, params).fetchall()
    return [
        FinancialFact(
            cik=r[0], metric=r[1], tag_used=r[2], value=r[3], unit=r[4],
            fiscal_year=r[5], fiscal_period=r[6], form=r[7],
            end_date=r[8], filed_date=r[9], coverage_quality=r[10],
        )
        for r in rows
    ]
