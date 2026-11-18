from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker


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
