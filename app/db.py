"""Short-lived SQLite connections; no network calls inside transactions."""

from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
import sqlite3


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=2, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=2000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise ValueError("数据库版本高于程序支持版本")
            if version == 0:
                sql = files("app").joinpath("migrations/001_initial.sql").read_text()
                try:
                    connection.executescript(
                        "BEGIN IMMEDIATE;\n" + sql + "\nPRAGMA user_version=1;\nCOMMIT;"
                    )
                except BaseException:
                    connection.rollback()
                    raise
