"""Test generator module for uni-test-sentinel."""

import json
import random
import uuid
from typing import Any

import aiosqlite

from src.grading import (
    calculate_grade,
    calculate_percentage,
    format_score,
    get_grade_label,
)
from src.models import ALLOWED_SECTIONS, SECTION_DISTRIBUTION

__all__ = [
    "SECTION_DISTRIBUTION",
    "calculate_grade",
    "calculate_percentage",
    "create_test_session",
    "generate_ticket_questions",
    "get_grade_label",
    "get_session_exam_data",
]


async def generate_ticket_questions(
    db: aiosqlite.Connection,
) -> list[dict[str, Any]]:
    """Selects exactly 20 questions balanced across the 6 allowed sections:

    - Section 1 (Введение в ИТ): 3 questions
    - Section 2 (Информация и ее обработка): 4 questions
    - Section 3 (Аппаратное обеспечение): 4 questions
    - Section 4 (Программное обеспечение): 3 questions
    - Section 7 (Сети и интернет): 3 questions
    - Section 8 (Информационная безопасность): 3 questions
    Total: 20 questions.
    For each question, shuffles answer choices randomly.
    """
    chosen_questions: list[dict[str, Any]] = []

    for section_id, count_needed in SECTION_DISTRIBUTION.items():
        cursor = await db.execute(
            """
            SELECT id, section, question_text, correct_answer, distractor1, distractor2, distractor3
            FROM questions
            WHERE section = ?
            ORDER BY RANDOM();
            """,
            (section_id,),
        )
        rows = await cursor.fetchall()
        if len(rows) < count_needed:
            raise ValueError(
                f"Недостаточно вопросов в разделе {section_id}: требуется {count_needed}, найдено {len(rows)}"
            )

        selected_rows = rows[:count_needed]
        for row in selected_rows:
            options = [
                row["correct_answer"].strip(),
                row["distractor1"].strip(),
                row["distractor2"].strip(),
                row["distractor3"].strip(),
            ]
            # Random shuffle options
            random.shuffle(options)

            chosen_questions.append(
                {
                    "question_id": row["id"],
                    "section": row["section"],
                    "section_name": ALLOWED_SECTIONS.get(row["section"], ""),
                    "question_text": row["question_text"].strip(),
                    "options": options,
                }
            )

    # Shuffle the overall question order so students in the same room don't have identical sequences
    random.shuffle(chosen_questions)

    # Assign question_order 1..20
    for idx, q in enumerate(chosen_questions, start=1):
        q["question_order"] = idx

    return chosen_questions


async def create_test_session(
    db: aiosqlite.Connection,
    fio: str,
    group_name: str,
    test_date: str,
    session_id: str | None = None,
) -> str:
    """Generates an individual 20-question test session in the database."""
    if not session_id:
        session_id = uuid.uuid4().hex[:12]

    # Generate 20 balanced questions
    ticket = await generate_ticket_questions(db)
    if len(ticket) != 20:
        raise ValueError(f"Билет должен содержать ровно 20 вопросов, получено: {len(ticket)}")

    # Insert test session
    await db.execute(
        """
        INSERT INTO test_sessions (
            session_id, fio, group_name, test_date, score, total, grade, is_completed
        ) VALUES (?, ?, ?, ?, 0.0, 10.0, 2, 0);
        """,
        (session_id, fio.strip(), group_name.strip(), test_date.strip()),
    )

    # Insert session questions
    records = [
        (
            session_id,
            q["question_id"],
            q["question_order"],
            json.dumps(q["options"], ensure_ascii=False),
            None,
            0,
        )
        for q in ticket
    ]
    await db.executemany(
        """
        INSERT INTO session_questions (
            session_id, question_id, question_order, options_json, selected_answer, is_correct
        ) VALUES (?, ?, ?, ?, ?, ?);
        """,
        records,
    )
    await db.commit()

    return session_id


async def get_session_exam_data(db: aiosqlite.Connection, session_id: str) -> dict[str, Any] | None:
    """Fetches session metadata and the ordered list of 20 questions for student exam interface."""
    cursor = await db.execute(
        """
        SELECT session_id, fio, group_name, test_date, score, total, grade, is_completed, created_at
        FROM test_sessions
        WHERE session_id = ?;
        """,
        (session_id,),
    )
    session = await cursor.fetchone()
    if not session:
        return None

    cursor = await db.execute(
        """
        SELECT 
            sq.question_id,
            sq.question_order,
            sq.options_json,
            sq.selected_answer,
            q.section,
            q.question_text
        FROM session_questions sq
        JOIN questions q ON sq.question_id = q.id
        WHERE sq.session_id = ?
        ORDER BY sq.question_order ASC;
        """,
        (session_id,),
    )
    rows = await cursor.fetchall()

    questions = []
    for r in rows:
        options = json.loads(r["options_json"])
        questions.append(
            {
                "question_id": r["question_id"],
                "question_order": r["question_order"],
                "section": r["section"],
                "section_name": ALLOWED_SECTIONS.get(r["section"], ""),
                "question_text": r["question_text"],
                "options": options,
                "selected_answer": r["selected_answer"],
            }
        )

    score_val = float(session["score"])
    total_val = float(session["total"])
    return {
        "session_id": session["session_id"],
        "fio": session["fio"],
        "group_name": session["group_name"],
        "test_date": session["test_date"],
        "is_completed": bool(session["is_completed"]),
        "score": score_val,
        "total": total_val,
        "formatted_score": format_score(score_val, total_val),
        "grade": session["grade"],
        "grade_label": get_grade_label(session["grade"]),
        "percentage": calculate_percentage(score_val, total_val),
        "created_at": session["created_at"],
        "questions": questions,
    }
