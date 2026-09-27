"""Database access: engine creation, schema management, idempotent writes."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Engine, Table, create_engine, delete, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from quantrisk.data import schema


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, future=True, pool_pre_ping=True)


def init_db(engine: Engine) -> None:
    schema.metadata.create_all(engine)


def postgres_ddl() -> str:
    """Render the schema as PostgreSQL DDL (used to generate sql/migrations/001_init.sql)."""
    dialect = postgresql.dialect()
    parts = ["-- Generated from quantrisk.data.schema. Do not edit by hand.\n"]
    for table in schema.metadata.sorted_tables:
        parts.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";\n")
        for idx in table.indexes:
            parts.append(str(CreateIndex(idx).compile(dialect=dialect)).strip() + ";\n")
    return "\n".join(parts)


def replace_rows(engine: Engine, table: Table, df: pd.DataFrame, where: dict[str, Any] | None = None,
                 where_in: dict[str, Iterable[Any]] | None = None) -> int:
    """Idempotent write: delete the slice identified by `where`/`where_in`, then insert.

    Re-running the same as-of date or run_id therefore replaces rows rather than
    duplicating them.
    """
    records = _records(df)
    with engine.begin() as conn:
        stmt = delete(table)
        for col, val in (where or {}).items():
            stmt = stmt.where(table.c[col] == _py(val))
        for col, vals in (where_in or {}).items():
            stmt = stmt.where(table.c[col].in_([_py(v) for v in vals]))
        if where or where_in:
            conn.execute(stmt)
        if records:
            # chunk to keep parameter counts reasonable on large inserts
            for i in range(0, len(records), 5000):
                conn.execute(table.insert(), records[i:i + 5000])
    return len(records)


def upsert_rows(engine: Engine, table: Table, df: pd.DataFrame) -> int:
    """Insert or update by primary key. Used for reference data, which other tables
    reference through foreign keys and therefore must never be deleted and re-inserted."""
    if not len(df):
        return 0
    records = _records(df)
    pk = [c.name for c in table.primary_key.columns]
    name = engine.dialect.name
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    elif name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert  # type: ignore[assignment]
    else:  # pragma: no cover
        raise NotImplementedError(name)
    stmt = dialect_insert(table)
    stmt = stmt.on_conflict_do_update(index_elements=pk,
                                      set_={c.name: stmt.excluded[c.name] for c in table.columns if c.name not in pk})
    with engine.begin() as conn:
        conn.execute(stmt, records)
    return len(records)


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame -> records with NaN/NaT mapped to None (SQL NULL) and numpy scalars unwrapped."""
    if not len(df):
        return []
    clean = df.astype(object).where(df.notna(), None)
    return [{k: _py(v) for k, v in r.items()} for r in clean.to_dict(orient="records")]


def _py(v: Any) -> Any:
    """numpy scalars -> Python scalars so drivers bind them correctly."""
    return v.item() if hasattr(v, "item") and not isinstance(v, (str, bytes)) else v


def read_df(engine: Engine, table: Table, **filters: Any) -> pd.DataFrame:
    stmt = select(table)
    for col, val in filters.items():
        stmt = stmt.where(table.c[col] == val)
    with engine.connect() as conn:
        return pd.DataFrame(conn.execute(stmt).mappings().all())


def read_sql(engine: Engine, sql: str, **params: Any) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.DataFrame(conn.execute(text(sql), params).mappings().all())
