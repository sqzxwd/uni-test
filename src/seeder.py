import asyncio
import json
import sys
from pathlib import Path

# Add project root to sys.path if needed
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.database import get_db_context, init_db
from src.models import ALLOWED_SECTIONS

SEED_DATA_PATH = ROOT_DIR / "src" / "data" / "seed_questions.json"


async def seed_database(db_path: str | None = None, clear_existing: bool = True) -> int:
    """Populates the database with questions strictly from allowed sections (1, 2, 3, 4, 7, 8)."""
    # Ensure database schema is ready
    if db_path:
        await init_db(db_path)
    else:
        await init_db()

    if not SEED_DATA_PATH.exists():
        raise FileNotFoundError(f"Файл с данными для сидера не найден: {SEED_DATA_PATH}")

    with open(SEED_DATA_PATH, encoding="utf-8") as f:
        questions_data = json.load(f)

    # Filter strictly allowed sections
    valid_questions = [q for q in questions_data if q["section"] in ALLOWED_SECTIONS]

    async with get_db_context(db_path) if db_path else get_db_context() as db:
        if clear_existing:
            await db.execute("DELETE FROM session_questions;")
            await db.execute("DELETE FROM test_sessions;")
            await db.execute("DELETE FROM questions;")
            await db.commit()

        # Check existing count
        cursor = await db.execute("SELECT COUNT(*) FROM questions;")
        (existing_count,) = await cursor.fetchone()

        if existing_count == 0 or clear_existing:
            records = [
                (
                    q["section"],
                    q["question_text"].strip(),
                    q["correct_answer"].strip(),
                    q["distractor1"].strip(),
                    q["distractor2"].strip(),
                    q["distractor3"].strip(),
                )
                for q in valid_questions
            ]
            await db.executemany(
                """
                INSERT INTO questions (
                    section, question_text, correct_answer, distractor1, distractor2, distractor3
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                records,
            )
            await db.commit()

        # Verify counts per section
        cursor = await db.execute(
            "SELECT section, COUNT(*) as cnt FROM questions GROUP BY section ORDER BY section;"
        )
        stats = await cursor.fetchall()
        print(f"Всего загружено вопросов: {len(valid_questions)}")
        for row in stats:
            sec_name = ALLOWED_SECTIONS.get(row["section"], "Неизвестно")
            print(f" - Раздел {row['section']} ({sec_name}): {row['cnt']} вопросов")

    return len(valid_questions)


def main() -> None:
    print("Запуск сидера вопросов uni-test-sentinel...")
    count = asyncio.run(seed_database())
    print(f"Успешно: база данных наполнена {count} вопросами.")


if __name__ == "__main__":
    main()
