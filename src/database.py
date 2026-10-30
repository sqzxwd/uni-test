import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import aiosqlite

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_NAME = os.getenv("DB_PATH", "test_platform.db")
DB_PATH = os.path.join(BASE_DIR, DB_NAME) if not os.path.isabs(DB_NAME) else DB_NAME


async def get_db_connection(db_path: str = DB_PATH) -> aiosqlite.Connection:
    """Creates and configures an aiosqlite connection with WAL mode and performance tuning."""
    db = await aiosqlite.connect(db_path)
    await db.execute("PRAGMA journal_mode=WAL;")
    await db.execute("PRAGMA synchronous=NORMAL;")
    await db.execute("PRAGMA busy_timeout=15000;")
    await db.execute("PRAGMA foreign_keys=ON;")
    await db.execute("PRAGMA cache_size=-64000;")
    db.row_factory = aiosqlite.Row
    return db


@asynccontextmanager
async def get_db_context(
    db_path: str = DB_PATH,
) -> AsyncGenerator[aiosqlite.Connection]:
    """Async context manager for DB connection, automatically closing it on exit."""
    db = await get_db_connection(db_path)
    try:
        yield db
    finally:
        await db.close()


async def get_db() -> AsyncGenerator[aiosqlite.Connection]:
    """FastAPI dependency for obtaining a database connection."""
    async with get_db_context() as db:
        yield db


async def init_db(db_path: str = DB_PATH) -> None:
    """Initializes tables and indexes in SQLite."""
    async with get_db_context(db_path) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            distractor1 TEXT NOT NULL,
            distractor2 TEXT NOT NULL,
            distractor3 TEXT NOT NULL
        );
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS test_sessions (
            session_id TEXT PRIMARY KEY,
            fio TEXT NOT NULL,
            group_name TEXT NOT NULL,
            test_date TEXT NOT NULL,
            score REAL DEFAULT 0.0,
            total REAL DEFAULT 10.0,
            grade INTEGER DEFAULT 2,
            is_completed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS session_questions (
            session_id TEXT NOT NULL,
            question_id INTEGER NOT NULL,
            question_order INTEGER NOT NULL,
            options_json TEXT NOT NULL,
            selected_answer TEXT,
            is_correct INTEGER DEFAULT 0,
            PRIMARY KEY (session_id, question_id),
            FOREIGN KEY (session_id) REFERENCES test_sessions (session_id) ON DELETE CASCADE,
            FOREIGN KEY (question_id) REFERENCES questions (id) ON DELETE RESTRICT
        );
        """)

        # Performance indexes
        await db.execute("CREATE INDEX IF NOT EXISTS idx_questions_section ON questions(section);")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_questions_order ON session_questions(session_id, question_order);"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_test_sessions_group ON test_sessions(group_name);"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_test_sessions_date ON test_sessions(test_date);"
        )
        await db.commit()


if __name__ == "__main__":
    import asyncio

    asyncio.run(init_db())
    print("База данных успешно инициализирована.")
