from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.errors import AppError
from app.models import DatabaseTarget


class Database:
    def __init__(self, url: str):
        self.engine, self.sessions = create_database(url)
        self.lock = RLock()
        self.target: dict | None = None

    @contextmanager
    def transaction(self):
        with self.lock:
            with self.sessions.begin() as session:
                if self.target is not None:
                    binding = session.get(DatabaseTarget, 1)
                    if binding is None:
                        session.add(DatabaseTarget(id=1, target_json=self.target))
                        session.flush()
                    elif binding.target_json != self.target:
                        raise AppError(
                            409,
                            "DATABASE_TARGET_MISMATCH",
                            "This database belongs to another Keitaro target; use its original configuration or a separate database",
                            outcome="not_applied",
                        )
                yield session


def create_database(url: str) -> tuple[Engine, sessionmaker[Session]]:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        raise ValueError("This application supports SQLite database URLs")
    if parsed.database and parsed.database != ":memory:":
        Path(parsed.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 30, "autocommit": False},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _record):
        previous = connection.autocommit
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()
        connection.autocommit = previous

    return engine, sessionmaker(engine, expire_on_commit=False)
