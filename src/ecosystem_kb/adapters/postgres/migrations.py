"""Versioned migration runner (Gate 1): explicit, ordered, recorded.

Replaces startup create_all() as the production schema mechanism. Each
migration is (version, name, sql-for-postgres, sql-for-sqlite); the runner
applies pending ones inside transactions and records them in
kb_schema_migrations. create_all() remains available for throwaway dev DBs
but production startup runs migrate().

SQLAlchemy metadata stays the source of truth for ORM models; migrations
must match it. tests/contract/test_migrations.py asserts parity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


def _resolve_migrations_dir() -> Path:
    """Locate migrations/ robustly: repo layout first, then cwd (containers
    COPY migrations/ to /app/migrations), then alongside the package."""
    candidates = [
        Path(__file__).resolve().parents[4] / "migrations",
        Path.cwd() / "migrations",
        Path(__file__).resolve().parents[1] / "migrations",
    ]
    for cand in candidates:
        if cand.is_dir() and any(cand.glob("*.sql")):
            return cand
    raise FileNotFoundError(
        "migrations/ not found; expected at repo root, CWD, or package dir"
    )


MIGRATIONS_DIR = _resolve_migrations_dir()


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    postgres: str
    sqlite: str


def _split_statements(sql: str) -> list[str]:
    """Split on ; after stripping -- comments (they may contain semicolons)."""
    without_comments = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    out = []
    for stmt in without_comments.split(";"):
        stmt = stmt.strip()
        if stmt:
            out.append(stmt)
    return out


def load_migrations() -> list[Migration]:
    """Parse migrations/*.sql. Filename format: NNN_name.sql (dialect-neutral)
    or NNN_name.postgres.sql / NNN_name.sqlite.sql."""
    found: dict[int, tuple[str, str | None, str | None]] = {}
    if not MIGRATIONS_DIR.exists():
        return []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = re.match(r"^(\d+)_([a-z0-9_]+?)(?:\.(postgres|sqlite))?\.sql$", path.name)
        if m is None:
            raise ValueError(f"migration filename not parseable: {path.name}")
        version = int(m.group(1))
        name = m.group(2)
        dialect = m.group(3)
        entry = found.setdefault(version, (name, None, None))
        if dialect is None:
            found[version] = (name, path.read_text(), path.read_text())
        elif dialect == "postgres":
            found[version] = (entry[0], path.read_text(), entry[2])
        else:
            found[version] = (entry[0], entry[1], path.read_text())
    migrations = []
    for version, (name, pg, lite) in sorted(found.items()):
        if pg is None or lite is None:
            raise ValueError(f"migration {version}_{name} missing a dialect file")
        migrations.append(Migration(version, name, pg, lite))
    return migrations


async def _ensure_migration_table(conn: AsyncConnection, is_sqlite: bool) -> None:
    if is_sqlite:
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS kb_schema_migrations ("
                "version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
        )
    else:
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS kb_schema_migrations ("
                "version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
        )


async def applied_versions(conn: AsyncConnection, is_sqlite: bool) -> set[int]:
    await _ensure_migration_table(conn, is_sqlite)
    rows = (await conn.execute(text("SELECT version FROM kb_schema_migrations"))).scalars()
    return set(int(v) for v in rows)


async def migrate(engine, *, dialect_sql: str | None = None) -> list[int]:
    """Apply pending migrations. Returns applied versions in order.

    dialect_sql: 'postgres' | 'sqlite' — normally inferred from the engine.
    Each migration commits atomically; a failure aborts only that migration.
    """
    is_sqlite = engine.dialect.name == "sqlite"
    migrations = load_migrations()
    async with engine.begin() as conn:
        done = await applied_versions(conn, is_sqlite)
    applied: list[int] = []
    for migration in migrations:
        if migration.version in done:
            continue
        sql = migration.sqlite if is_sqlite else migration.postgres
        async with engine.begin() as conn:
            for stmt in _split_statements(sql):
                await conn.execute(text(stmt))
            if is_sqlite:
                await conn.execute(
                    text(
                        "INSERT INTO kb_schema_migrations (version, name, applied_at) "
                        "VALUES (:v, :n, datetime('now'))"
                    ),
                    {"v": migration.version, "n": migration.name},
                )
            else:
                await conn.execute(
                    text(
                        "INSERT INTO kb_schema_migrations (version, name, applied_at) "
                        "VALUES (:v, :n, now())"
                    ),
                    {"v": migration.version, "n": migration.name},
                )
        applied.append(migration.version)
    return applied


def latest_version() -> int:
    migrations = load_migrations()
    return migrations[-1].version if migrations else 0
