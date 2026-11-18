"""SQLite lifecycle, versioned migrations and consistent offline/online backups."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from importlib.resources import files
import os
from pathlib import Path
import sqlite3
import tempfile

from . import conf

SCHEMA_VERSION = 1


class DatabaseError(RuntimeError):
    """Database lifecycle operation could not safely proceed."""


def default_db_path() -> Path:
    return Path(conf.DB_PATH).expanduser().resolve()


def legacy_db_path() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return (base / "schwab-cli" / "positions.sqlite3").resolve()


def schema_sql() -> str:
    return files("schwab_cli").joinpath("schema", "schema.sql").read_text(encoding="utf-8")


def _path(path: str | Path | None) -> Path:
    return default_db_path() if path is None else Path(path).expanduser().resolve()


def _open(path: Path, mode: str = "rw") -> sqlite3.Connection:
    db = sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True,
                         timeout=conf.DB_TIMEOUT_SECONDS)
    db.row_factory = sqlite3.Row
    return db


def _version(db: sqlite3.Connection) -> int:
    return db.execute("PRAGMA user_version").fetchone()[0]


def _signature(db: sqlite3.Connection) -> list:
    return [tuple(row) for row in db.execute(
        "SELECT type, name, tbl_name FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    )]


def _validate_v1(db: sqlite3.Connection) -> None:
    # Compare structural metadata rather than SQL whitespace or legacy IF NOT EXISTS.
    with closing(sqlite3.connect(":memory:")) as expected:
        expected.executescript(schema_sql())
        if _signature(db) != _signature(expected):
            raise DatabaseError("Unexpected database schema; refusing to modify it")
        for table in ("positions", "pending_orders"):
            for pragma in ("table_info", "index_list"):
                actual = [tuple(row) for row in db.execute(f"PRAGMA {pragma}({table})")]
                wanted = [tuple(row) for row in expected.execute(f"PRAGMA {pragma}({table})")]
                if actual != wanted:
                    raise DatabaseError(f"Unexpected {table} schema; refusing to modify it")
        for index in ("active_instrument",):
            if ([tuple(row) for row in db.execute(f"PRAGMA index_xinfo({index})")]
                    != [tuple(row) for row in expected.execute(f"PRAGMA index_xinfo({index})")]):
                raise DatabaseError(f"Unexpected {index} definition")
            actual_sql = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (index,)).fetchone()[0]
            predicate = actual_sql.upper().partition("WHERE")[2].replace(" ", "").strip()
            if predicate != "ACTIVE=1":
                raise DatabaseError("Unexpected active_instrument predicate")


def _check_version(db: sqlite3.Connection) -> int:
    version = _version(db)
    if version < 0 or version > SCHEMA_VERSION:
        raise DatabaseError(f"Unsupported schema version {version}; expected <= {SCHEMA_VERSION}")
    return version


def _guard_missing(path: Path) -> None:
    legacy = legacy_db_path()
    if not path.exists() and legacy != path and legacy.exists():
        raise DatabaseError(
            f"Legacy database exists at {legacy}. Stop running CLI processes, "
            f"then use schwab db backup '{path}' --source '{legacy}' "
            "and schwab db migrate. Refusing to create an empty database."
        )


def _apply_migrations(db: sqlite3.Connection) -> int:
    # All DDL and version changes are transactional; never use executescript
    # on the live connection because it implicitly commits the transaction.
    db.execute("BEGIN IMMEDIATE")
    try:
        version = _check_version(db)
        if version == 0:
            if _signature(db):
                _validate_v1(db)  # Adopt the original unversioned schema in place.
            else:
                sql = files("schwab_cli").joinpath(
                    "schema", "migrations", "001_initial.sql").read_text(encoding="utf-8")
                statement = ""
                for line in sql.splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        db.execute(statement)
                        statement = ""
                if statement.strip():
                    raise DatabaseError("Incomplete migration SQL")
                _validate_v1(db)
            db.execute("PRAGMA user_version=1")
        db.commit()
        return _version(db)
    except BaseException:
        db.rollback()
        raise


def initialize(path: str | Path | None = None) -> int:
    """Explicitly create a new database or verify an already initialized one."""
    target = _path(path)
    if target.exists():
        with closing(_open(target)) as db:
            version = _check_version(db)
            if version != SCHEMA_VERSION:
                raise DatabaseError("Database needs migration; run schwab db migrate")
            _validate_v1(db)
            return version
    _guard_missing(target)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Exclusive creation prevents overwriting anything, even during a race.
    try:
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass  # Another initializer is racing; SQLite serializes migration.
    else:
        os.close(fd)
    with closing(_open(target)) as db:
        return _apply_migrations(db)


def migrate(path: str | Path | None = None) -> int:
    target = _path(path)
    if not target.exists():
        raise DatabaseError("Database missing; run schwab db init or relocate an existing database")
    with closing(_open(target)) as db:
        return _apply_migrations(db)


def status(path: str | Path | None = None) -> dict:
    target = _path(path)
    result = {"path": str(target), "exists": target.exists(),
              "schema_version": None, "expected_version": SCHEMA_VERSION,
              "legacy_path": str(legacy_db_path()), "legacy_exists": legacy_db_path().exists()}
    if target.exists():
        with closing(_open(target, "ro")) as db:
            result["schema_version"] = _version(db)
            result["journal_mode"] = db.execute("PRAGMA journal_mode").fetchone()[0]
    return result


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open a current database. Preserve first-use creation for new installations."""
    target = _path(path)
    if not target.exists():
        initialize(target)
    db = _open(target)
    try:
        version = _check_version(db)
        if version != SCHEMA_VERSION:
            raise DatabaseError("Database needs migration; run schwab db migrate")
        mode = conf.DB_JOURNAL_MODE
        if mode not in {"WAL", "DELETE"}:
            raise DatabaseError("Invalid configured journal mode")
        current = db.execute("PRAGMA journal_mode").fetchone()[0]
        if current.upper() != mode:
            actual = db.execute(f"PRAGMA journal_mode={mode}").fetchone()[0]
            if actual.upper() != mode:
                raise DatabaseError("Could not apply configured journal mode")
        db.execute("PRAGMA foreign_keys=ON")
        return db
    except BaseException:
        db.close()
        raise


def backup(destination: str | Path | None = None, *,
           source: str | Path | None = None) -> Path:
    """Snapshot a database including WAL commits; publish without overwriting."""
    src = _path(source)
    if not src.is_file():
        raise DatabaseError(f"Source database does not exist: {src}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    name = f"{src.stem}-{stamp}.sqlite3"
    if destination is None:
        dest = Path(conf.DB_BACKUP_DIR).expanduser() / name
    else:
        raw = str(destination)
        dest = Path(raw).expanduser()
        if dest.is_dir() or raw.endswith(("/", os.sep)):
            dest = dest / name
    dest = dest.resolve()
    if dest == src or dest.exists():
        raise DatabaseError(f"Backup destination already exists: {dest}")
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".schwab-backup-", dir=dest.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        with closing(_open(src, "ro")) as original, closing(_open(temp)) as copy:
            original.backup(copy)
            if copy.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise DatabaseError("Backup integrity check failed")
            copy.execute("PRAGMA journal_mode=DELETE")
        # Same-directory hard link is atomic and fails rather than overwriting.
        try:
            os.link(temp, dest)
        except FileExistsError as exc:
            raise DatabaseError(f"Backup destination already exists: {dest}") from exc
        return dest
    finally:
        temp.unlink(missing_ok=True)
