"""Unit and integration tests for uni-test-sentinel."""

import asyncio
import io
import os
from collections import Counter

import pytest
from openpyxl import load_workbook
from starlette.testclient import TestClient

from src.database import get_db_context, init_db
from src.grading import (
    calculate_grade,
    calculate_percentage,
    format_score,
    grade_session_submission,
)
from src.main import app
from src.models import ALLOWED_GROUPS, ALLOWED_SECTIONS
from src.seeder import seed_database
from src.test_generator import (
    create_test_session,
    generate_ticket_questions,
    get_session_exam_data,
)


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Seeds the test database before running tests."""
    asyncio.run(init_db())
    asyncio.run(seed_database())


# ==========================================
# 1. QUESTION BANK & SEEDER TESTS
# ==========================================


@pytest.mark.asyncio
async def test_question_bank_allowed_sections():
    """Verifies that all questions in the database belong strictly to the 6 allowed sections."""
    async with get_db_context() as db:
        cursor = await db.execute("SELECT DISTINCT section FROM questions;")
        db_sections = {row[0] for row in await cursor.fetchall()}

    assert db_sections.issubset(set(ALLOWED_SECTIONS.keys())), (
        f"Found forbidden sections in DB: {db_sections - set(ALLOWED_SECTIONS.keys())}"
    )
    assert db_sections == {1, 2, 3, 4, 7, 8}


@pytest.mark.asyncio
async def test_question_options_integrity():
    """Verifies that each question has 4 non-empty, distinct options."""
    async with get_db_context() as db:
        cursor = await db.execute(
            "SELECT id, question_text, correct_answer, distractor1, distractor2, distractor3 FROM questions;"
        )
        rows = await cursor.fetchall()

    assert len(rows) > 0, "Questions table is empty!"
    for r in rows:
        q_text = r["question_text"].strip()
        ans = r["correct_answer"].strip()
        d1 = r["distractor1"].strip()
        d2 = r["distractor2"].strip()
        d3 = r["distractor3"].strip()

        assert q_text, f"Empty question text for question ID {r['id']}"
        assert ans, f"Empty correct answer for question ID {r['id']}"
        assert d1 and d2 and d3, f"Empty distractor for question ID {r['id']}"

        opts = [ans, d1, d2, d3]
        assert len(set(opts)) == 4, f"Question ID {r['id']} has duplicate options: {opts}"


# ==========================================
# 2. TICKET GENERATOR TESTS
# ==========================================


@pytest.mark.asyncio
async def test_ticket_exact_20_questions_and_distribution():
    """Verifies that ticket generator picks exactly 20 questions with exact section distribution:

    - Section 1: 3
    - Section 2: 4
    - Section 3: 4
    - Section 4: 3
    - Section 7: 3
    - Section 8: 3
    """
    async with get_db_context() as db:
        for _ in range(5):  # test across multiple random tickets
            ticket = await generate_ticket_questions(db)
            assert len(ticket) == 20, f"Expected 20 questions, got {len(ticket)}"

            counts = Counter(q["section"] for q in ticket)
            assert counts[1] == 3, f"Section 1 expected 3, got {counts[1]}"
            assert counts[2] == 4, f"Section 2 expected 4, got {counts[2]}"
            assert counts[3] == 4, f"Section 3 expected 4, got {counts[3]}"
            assert counts[4] == 3, f"Section 4 expected 3, got {counts[4]}"
            assert counts[7] == 3, f"Section 7 expected 3, got {counts[7]}"
            assert counts[8] == 3, f"Section 8 expected 3, got {counts[8]}"

            # Verify every question has 4 shuffled options
            for q in ticket:
                assert len(q["options"]) == 4
                assert len(set(q["options"])) == 4


# ==========================================
# 3. GRADING SCALE & 0.5 POINT TESTS
# ==========================================


def test_grading_scale_boundaries():
    """Tests the new 0.5-point grading scale (max 10.0 points):

    - 9.0 – 10.0: 5 (Отлично)
    - 7.0 – 8.5:  4 (Хорошо)
    - 5.0 – 6.5:  3 (Удовлетворительно)
    - 0.0 – 4.5:  2 (Неудовлетворительно)
    """
    # Grade 5: 9.0 - 10.0
    assert calculate_grade(10.0) == 5
    assert calculate_grade(9.5) == 5
    assert calculate_grade(9.0) == 5

    # Grade 4: 7.0 - 8.5
    assert calculate_grade(8.5) == 4
    assert calculate_grade(8.0) == 4
    assert calculate_grade(7.5) == 4
    assert calculate_grade(7.0) == 4

    # Grade 3: 5.0 - 6.5
    assert calculate_grade(6.5) == 3
    assert calculate_grade(6.0) == 3
    assert calculate_grade(5.5) == 3
    assert calculate_grade(5.0) == 3

    # Grade 2: 0.0 - 4.5
    assert calculate_grade(4.5) == 2
    assert calculate_grade(4.0) == 2
    assert calculate_grade(3.0) == 2
    assert calculate_grade(1.5) == 2
    assert calculate_grade(0.5) == 2
    assert calculate_grade(0.0) == 2


def test_percentage_and_format_score():
    """Tests percentage computation and score formatting as 'X / 10' or 'X.X / 10.0'."""
    assert calculate_percentage(10.0, 10.0) == 100.0
    assert calculate_percentage(9.0, 10.0) == 90.0
    assert calculate_percentage(7.5, 10.0) == 75.0
    assert calculate_percentage(5.0, 10.0) == 50.0
    assert calculate_percentage(0.0, 10.0) == 0.0

    assert format_score(10.0) == "10 / 10"
    assert format_score(9.0) == "9 / 10"
    assert format_score(8.5) == "8.5 / 10.0"
    assert format_score(7.0) == "7 / 10"
    assert format_score(4.5) == "4.5 / 10.0"


# ==========================================
# 4. FULL EXAM FLOW & REPEAT SUBMISSION PROTECTION
# ==========================================


@pytest.mark.asyncio
async def test_session_lifecycle_and_double_submit_protection():
    """Tests session creation, submission grading with 0.5-point scale, and double-submit protection."""
    async with get_db_context() as db:
        # 1. Create session
        sid = await create_test_session(
            db=db,
            fio="Тестовый Студент Сергеевич",
            group_name="161-51",
            test_date="2026-10-30",
        )
        assert sid

        # 2. Fetch exam data
        exam_data = await get_session_exam_data(db, sid)
        assert exam_data is not None
        assert exam_data["is_completed"] is False
        assert len(exam_data["questions"]) == 20
        assert exam_data["total"] == 10.0

        # 3. Simulate answering: 18 correct answers (18 * 0.5 = 9.0 points -> Grade 5)
        cursor = await db.execute(
            """
            SELECT sq.question_id, q.correct_answer, q.distractor1
            FROM session_questions sq
            JOIN questions q ON sq.question_id = q.id
            WHERE sq.session_id = ?
            ORDER BY sq.question_order ASC;
            """,
            (sid,),
        )
        q_rows = await cursor.fetchall()

        submitted_answers = {}
        for idx, row in enumerate(q_rows):
            qid = row["question_id"]
            if idx < 18:
                # 18 correct answers
                submitted_answers[qid] = row["correct_answer"]
            else:
                # 2 wrong answers
                submitted_answers[qid] = row["distractor1"]

        # 4. Grade submission with silent focus_loss_count = 2
        result = await grade_session_submission(db, sid, submitted_answers, focus_loss_count=2)
        assert result["score"] == 9.0
        assert result["total"] == 10.0
        assert result["grade"] == 5
        assert result["is_completed"] is True
        assert result["already_submitted"] is False
        assert result["formatted_score"] == "9 / 10"
        assert result["focus_loss_count"] == 2

        # 5. Test Double-Submit Protection:
        # A second submission attempt with 0 answers must NOT downgrade or alter the saved grade!
        second_result = await grade_session_submission(db, sid, {})
        assert second_result["already_submitted"] is True
        assert second_result["score"] == 9.0
        assert second_result["grade"] == 5

        # Verify DB directly
        cursor = await db.execute(
            "SELECT score, grade, is_completed, focus_loss_count FROM test_sessions WHERE session_id = ?;",
            (sid,),
        )
        db_session = await cursor.fetchone()
        assert float(db_session["score"]) == 9.0
        assert db_session["grade"] == 5
        assert db_session["is_completed"] == 1
        assert db_session["focus_loss_count"] == 2


# ==========================================
# 5. FASTAPI HTTP INTEGRATION TESTS (40-MIN TIMER & CLOUDFLARE QR)
# ==========================================


def test_http_index_and_registration_flow():
    """Tests HTTP registration, exam page (40-minute timer), submit, and finish redirect."""
    client = TestClient(app)

    # 1. GET /
    res = client.get("/")
    assert res.status_code == 200
    assert "Информационные технологии" in res.text
    assert "40 минут" in res.text
    for g in ALLOWED_GROUPS:
        assert g in res.text

    # 2. POST /start validation failure (single word name)
    res_bad = client.post(
        "/start",
        data={
            "fio": "Иванов",
            "group_name": "161-51",
            "test_date": "2026-10-30",
        },
    )
    assert res_bad.status_code == 400

    # 3. POST /start valid
    res_start = client.post(
        "/start",
        data={
            "fio": "Алексеев Алексей Алексеевич",
            "group_name": "161-55",
            "test_date": "2026-10-30",
        },
        follow_redirects=False,
    )
    assert res_start.status_code == 303
    redirect_url = res_start.headers["Location"]
    assert redirect_url.startswith("/exam/")
    session_id = redirect_url.split("/exam/")[1]

    # 4. GET /exam/{session_id} - check 40:00 timer display and anti-cheat elements
    res_exam = client.get(f"/exam/{session_id}")
    assert res_exam.status_code == 200
    assert "Алексеев Алексей Алексеевич" in res_exam.text
    assert "161-55" in res_exam.text
    assert "40:00" in res_exam.text
    assert "focus_loss_count" in res_exam.text
    assert "exam-privacy-shield" in res_exam.text
    assert "Завершить тест" in res_exam.text

    # 5. POST /submit/{session_id} with focus_loss_count
    res_submit = client.post(
        f"/submit/{session_id}",
        data={"focus_loss_count": "1"},
        follow_redirects=False,
    )
    assert res_submit.status_code == 303
    assert res_submit.headers["Location"] == "/finish"

    # 6. GET /finish
    res_finish = client.get("/finish")
    assert res_finish.status_code == 200
    assert "Тест успешно сдан" in res_finish.text

    # 7. Accessing exam again after completion should redirect to /finish
    res_exam_again = client.get(f"/exam/{session_id}", follow_redirects=False)
    assert res_exam_again.status_code == 303
    assert res_exam_again.headers["Location"] == "/finish"


def test_admin_screen_and_qr_with_target_url():
    """Tests projector screen with 3 columns, grading criteria and target_url query parameter."""
    client = TestClient(app)

    # 1. Screen template contains 3-column items, tunnel input, and grading criteria
    res_screen = client.get("/admin/screen")
    assert res_screen.status_code == 200
    assert "Экран аудитории" in res_screen.text
    assert "7 учебных групп" in res_screen.text
    assert "tunnelUrlInput" in res_screen.text
    assert "Критерии оценивания" in res_screen.text
    assert "9.0 – 10.0" in res_screen.text
    assert "0.0 – 4.5" in res_screen.text
    assert "uni_test_tunnel_url" in res_screen.text

    # 2. QR code with target_url parameter (Cloudflare tunnel)
    res_qr_target = client.get("/admin/qr?target_url=https://uni-chem.trycloudflare.com/")
    assert res_qr_target.status_code == 200
    assert res_qr_target.headers["content-type"] == "image/png"
    assert len(res_qr_target.content) > 100

    # 3. QR code with fallback default
    res_qr_default = client.get("/admin/qr")
    assert res_qr_default.status_code == 200
    assert res_qr_default.headers["content-type"] == "image/png"


def test_admin_dashboard_auth_and_export():
    """Tests teacher dashboard authentication, sorting, filtering, and Excel export."""
    client = TestClient(app)
    admin_pwd = os.getenv("ADMIN_PASSWORD", "admin_chem_tech")

    # 1. Unauthenticated request to /admin redirects to login
    res_unauth = client.get("/admin", headers={"Accept": "text/html"}, follow_redirects=False)
    assert res_unauth.status_code == 307
    assert res_unauth.headers["Location"] == "/admin/login"

    # 2. Login via form
    res_login = client.post(
        "/admin/login",
        data={"password": admin_pwd},
        follow_redirects=False,
    )
    assert res_login.status_code == 303
    assert res_login.headers["Location"] == "/admin"
    admin_cookie = res_login.cookies.get("uni_admin_session")
    assert admin_cookie == admin_pwd

    # 3. Authenticated dashboard access
    client.cookies.set("uni_admin_session", admin_pwd)
    res_dash = client.get("/admin")
    assert res_dash.status_code == 200
    assert "Панель преподавателя" in res_dash.text
    assert "Сдало студентов" in res_dash.text
    assert "Сворачиваний" in res_dash.text
    assert "из 10.0" in res_dash.text

    # 4. Filter by group
    res_filter = client.get("/admin?group=161-51")
    assert res_filter.status_code == 200

    # 5. Export to Excel
    res_export = client.get("/admin/export")
    assert res_export.status_code == 200
    assert (
        res_export.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # Verify Excel file structure using openpyxl
    wb = load_workbook(io.BytesIO(res_export.content))
    assert "Ведомость тестирования" in wb.sheetnames
    ws = wb["Ведомость тестирования"]
    # Check headers in row 4
    headers = [cell.value for cell in ws[4]]
    assert headers == [
        "№ п/п",
        "Дата",
        "Группа",
        "ФИО",
        "Баллы",
        "Процент",
        "Оценка",
    ]


def test_student_results_review_and_security():
    """Tests the detailed results breakdown screen and cookie security isolation."""
    client = TestClient(app)

    # 1. Register student 1
    res1 = client.post(
        "/start",
        data={
            "fio": "Смирнов Иван Алексеевич",
            "group_name": "161-61",
            "test_date": "2026-11-01",
        },
        follow_redirects=False,
    )
    assert res1.status_code == 303
    s1_id = res1.headers["Location"].split("/exam/")[1]

    # Submit test with answers
    exam1 = client.get(f"/exam/{s1_id}")
    assert exam1.status_code == 200

    # Submit 15 questions
    submit_data = {"focus_loss_count": "0"}
    res1_sub = client.post(
        f"/submit/{s1_id}",
        data=submit_data,
        follow_redirects=False,
    )
    assert res1_sub.status_code == 303
    assert res1_sub.headers["Location"] == "/finish"

    # 2. Check /finish renders detailed review
    res1_finish = client.get("/finish")
    assert res1_finish.status_code == 200
    assert "Тест успешно сдан! Разбор работы" in res1_finish.text
    assert "Смирнов Иван Алексеевич" in res1_finish.text
    assert "161-61" in res1_finish.text
    assert "Итоговый балл" in res1_finish.text
    assert "Разбор вопросов и ошибок" in res1_finish.text
    assert "Вопрос 1" in res1_finish.text
    assert "Правильный ответ:" in res1_finish.text or "Ваш ответ:" in res1_finish.text

    # 3. Check /result/{s1_id} works for student 1
    res1_result = client.get(f"/result/{s1_id}")
    assert res1_result.status_code == 200
    assert "Смирнов Иван Алексеевич" in res1_result.text

    # 4. Student 2 registers in a new client session
    client2 = TestClient(app)
    res2 = client2.post(
        "/start",
        data={
            "fio": "Петрова Анна Сергеевна",
            "group_name": "161-71",
            "test_date": "2026-11-01",
        },
        follow_redirects=False,
    )
    s2_id = res2.headers["Location"].split("/exam/")[1]
    client2.post(f"/submit/{s2_id}", data={}, follow_redirects=False)

    # Student 2 tries to view Student 1's results using /result/{s1_id} -> must be 403 Forbidden!
    res_forbidden = client2.get(f"/result/{s1_id}")
    assert res_forbidden.status_code == 403
    assert "Доступ запрещен" in res_forbidden.text

    # 5. Admin can view any student's /result/{session_id}
    admin_client = TestClient(app)
    admin_pwd = os.getenv("ADMIN_PASSWORD", "admin_chem_tech")
    admin_client.cookies.set("uni_admin_session", admin_pwd)
    res_admin_view = admin_client.get(f"/result/{s1_id}")
    assert res_admin_view.status_code == 200
    assert "Смирнов Иван Алексеевич" in res_admin_view.text
