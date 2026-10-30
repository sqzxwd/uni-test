"""Grading module for uni-test-sentinel."""

from typing import Any

import aiosqlite


def calculate_grade(score: float, total: float = 10.0) -> int:
    """Calculates the academic grade (2, 3, 4, 5) based on the test score (max 10.0).

    Scale:
    - 9.0 – 10.0 -> 5 (отлично)
    - 7.0 – 8.5  -> 4 (хорошо)
    - 5.0 – 6.5  -> 3 (удовлетворительно)
    - 0.0 – 4.5  -> 2 (неудовлетворительно)
    """
    if score >= 9.0:
        return 5
    elif score >= 7.0:
        return 4
    elif score >= 5.0:
        return 3
    else:
        return 2


def get_grade_label(grade: int) -> str:
    """Returns human-readable text for the numeric grade."""
    labels = {
        5: "Отлично",
        4: "Хорошо",
        3: "Удовлетворительно",
        2: "Неудовлетворительно",
    }
    return labels.get(grade, "Не определено")


def calculate_percentage(score: float, total: float = 10.0) -> float:
    """Calculates the percentage score (0.0 to 100.0)."""
    if total <= 0:
        return 0.0
    return round((score / total) * 100, 1)


def format_score(score: float, total: float = 10.0) -> str:
    """Formats score as 'X / 10' or 'X.X / 10.0'."""
    if score % 1 == 0:
        return f"{int(score)} / 10"
    return f"{score:.1f} / 10.0"


async def grade_session_submission(
    db: aiosqlite.Connection,
    session_id: str,
    submitted_answers: dict[int, str],
    focus_loss_count: int = 0,
) -> dict[str, Any]:
    """Grades a student's session submission in an atomic transaction.

    Protects against double submission: if already completed, returns the existing record.
    """
    # Check session status
    cursor = await db.execute(
        "SELECT session_id, fio, group_name, test_date, score, total, grade, is_completed, focus_loss_count "
        "FROM test_sessions WHERE session_id = ?;",
        (session_id,),
    )
    session = await cursor.fetchone()
    if not session:
        raise ValueError(f"Сессия тестирования '{session_id}' не найдена.")

    if session["is_completed"]:
        # Double-submit protection: return existing results without re-evaluating
        return {
            "session_id": session_id,
            "fio": session["fio"],
            "group_name": session["group_name"],
            "score": float(session["score"]),
            "total": float(session["total"]),
            "grade": session["grade"],
            "grade_label": get_grade_label(session["grade"]),
            "percentage": calculate_percentage(float(session["score"]), float(session["total"])),
            "formatted_score": format_score(float(session["score"]), float(session["total"])),
            "focus_loss_count": session["focus_loss_count"] or 0,
            "is_completed": True,
            "already_submitted": True,
        }

    # Fetch questions for this session with correct answers
    cursor = await db.execute(
        """
        SELECT sq.question_id, q.correct_answer
        FROM session_questions sq
        JOIN questions q ON sq.question_id = q.id
        WHERE sq.session_id = ?;
        """,
        (session_id,),
    )
    rows = await cursor.fetchall()
    if not rows:
        raise ValueError(f"Вопросы для сессии '{session_id}' не найдены.")

    count_correct = 0

    # Process answers
    update_data = []
    for row in rows:
        qid = row["question_id"]
        correct_answer = row["correct_answer"].strip()
        selected = submitted_answers.get(qid)
        if selected is not None:
            selected = selected.strip()

        is_correct = 1 if (selected is not None and selected == correct_answer) else 0
        if is_correct:
            count_correct += 1

        update_data.append((selected, is_correct, session_id, qid))

    # Calculate score: 0.5 points per correct question, max 10.0
    score = round(count_correct * 0.5, 1)
    total = 10.0

    # Calculate grade
    grade = calculate_grade(score, total)

    # Update session_questions in batch
    await db.executemany(
        """
        UPDATE session_questions
        SET selected_answer = ?, is_correct = ?
        WHERE session_id = ? AND question_id = ?;
        """,
        update_data,
    )

    # Update test_sessions
    await db.execute(
        """
        UPDATE test_sessions
        SET score = ?, total = ?, grade = ?, is_completed = 1, focus_loss_count = ?
        WHERE session_id = ?;
        """,
        (score, total, grade, focus_loss_count, session_id),
    )
    await db.commit()

    return {
        "session_id": session_id,
        "fio": session["fio"],
        "group_name": session["group_name"],
        "score": score,
        "total": total,
        "grade": grade,
        "grade_label": get_grade_label(grade),
        "percentage": calculate_percentage(score, total),
        "formatted_score": format_score(score, total),
        "focus_loss_count": focus_loss_count,
        "is_completed": True,
        "already_submitted": False,
    }
