"""Main application module for uni-test-sentinel."""

import base64
import io
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite
import qrcode
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.database import get_db, init_db
from src.excel_export import generate_excel_report
from src.grading import (
    calculate_percentage,
    format_score,
    grade_session_submission,
)
from src.models import ALLOWED_GROUPS, ALLOWED_SECTIONS
from src.seeder import seed_database
from src.test_generator import (
    create_test_session,
    get_session_exam_data,
    get_session_results_data,
)

BASE_DIR = Path(__file__).resolve().parent.parent
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin_chem_tech")
ADMIN_COOKIE_NAME = "uni_admin_session"
STUDENT_COOKIE_NAME = "student_session"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure DB is initialized
    await init_db()
    # Check if questions exist; if empty, seed automatically
    from src.database import get_db_context

    async with get_db_context() as db:
        cursor = await db.execute("SELECT COUNT(*) FROM questions;")
        (count,) = await cursor.fetchone()
        if count == 0:
            print("База вопросов пуста. Автоматический запуск сидера...")
            await seed_database()
    yield


app = FastAPI(
    title="uni-test-sentinel",
    description="Система аудиторного тестирования студентов 1 курса по дисциплине «Информационные технологии»",
    version="1.0.0",
    lifespan=lifespan,
)

# Static and Templates
static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def is_admin_authenticated(request: Request) -> bool:
    """Verifies teacher credentials via Cookie, Basic Auth, or query parameter."""
    # 1. Check Cookie
    cookie_val = request.cookies.get(ADMIN_COOKIE_NAME)
    if cookie_val == ADMIN_PASSWORD:
        return True

    # 2. Check Query parameter
    query_auth = request.query_params.get("auth") or request.query_params.get("token")
    if query_auth == ADMIN_PASSWORD:
        return True

    # 3. Check Basic Auth header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Basic "):
        try:
            encoded = auth_header.split(" ", 1)[1]
            decoded = base64.b64decode(encoded).decode("utf-8")
            parts = decoded.split(":", 1)
            pwd = parts[1] if len(parts) > 1 else parts[0]
            if pwd == ADMIN_PASSWORD:
                return True
        except (ValueError, UnicodeDecodeError):
            return False

    return False


async def require_admin(request: Request):
    """Dependency that enforces admin authentication."""
    if not is_admin_authenticated(request):
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            raise HTTPException(
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers={"Location": "/admin/login"},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Необходима авторизация преподавателя",
            headers={"WWW-Authenticate": 'Basic realm="Admin Sentinel"'},
        )


# ==========================================
# STUDENT ROUTES
# ==========================================


@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    """Registration page for students: FIO, academic group, and test date."""
    today = datetime.now().strftime("%Y-%m-%d")
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "groups": ALLOWED_GROUPS,
            "today_date": today,
            "error": None,
        },
    )


@app.post("/start")
async def start_exam(
    request: Request,
    fio: str = Form(...),
    group_name: str = Form(...),
    test_date: str = Form(...),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Validates registration and generates a balanced 20-question test session."""
    cleaned_fio = fio.strip()
    parts = cleaned_fio.split()
    if len(parts) < 2 or len(cleaned_fio) < 3:
        today = datetime.now().strftime("%Y-%m-%d")
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "groups": ALLOWED_GROUPS,
                "today_date": today,
                "error": "Пожалуйста, введите Фамилию и Имя корректно (минимум 2 слова).",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if group_name not in ALLOWED_GROUPS:
        today = datetime.now().strftime("%Y-%m-%d")
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "groups": ALLOWED_GROUPS,
                "today_date": today,
                "error": "Выберите группу из предложенного списка.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    session_id = await create_test_session(
        db=db,
        fio=cleaned_fio,
        group_name=group_name,
        test_date=test_date,
    )

    response = RedirectResponse(
        url=f"/exam/{session_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.set_cookie(
        key=STUDENT_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        max_age=86400 * 30,
    )
    return response


@app.get("/exam/{session_id}", response_class=HTMLResponse)
async def exam_page(
    request: Request,
    session_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Exam interface: 20 balanced questions, 25-minute timer, touch radio options."""
    session_data = await get_session_exam_data(db, session_id)
    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Сессия тестирования не найдена.",
        )

    if session_data["is_completed"]:
        response = RedirectResponse(
            url="/finish",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        response.set_cookie(
            key=STUDENT_COOKIE_NAME,
            value=session_id,
            httponly=True,
            samesite="lax",
            max_age=86400 * 30,
        )
        return response

    return templates.TemplateResponse(
        request=request,
        name="exam.html",
        context={"session": session_data},
    )


@app.post("/submit/{session_id}")
async def submit_exam(
    request: Request,
    session_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Receives student answers, grades atomically in SQLite WAL, and redirects to finish."""
    form_data = await request.form()
    submitted_answers: dict[int, str] = {}

    for key, val in form_data.items():
        if key.startswith("q_"):
            try:
                qid = int(key[2:])
                submitted_answers[qid] = str(val)
            except ValueError:
                continue

    focus_loss_raw = form_data.get("focus_loss_count", 0)
    try:
        focus_loss_count = max(0, int(focus_loss_raw))
    except (ValueError, TypeError):
        focus_loss_count = 0

    try:
        await grade_session_submission(
            db=db,
            session_id=session_id,
            submitted_answers=submitted_answers,
            focus_loss_count=focus_loss_count,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    response = RedirectResponse(
        url="/finish",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.set_cookie(
        key=STUDENT_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        max_age=86400 * 30,
    )
    return response


@app.get("/finish", response_class=HTMLResponse)
@app.get("/result/{session_id}", response_class=HTMLResponse)
async def finish_page(
    request: Request,
    session_id: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Detailed exam review page showing score, grade, correctness, and explanations."""
    target_session_id = (
        session_id
        or request.query_params.get("session_id")
        or request.cookies.get(STUDENT_COOKIE_NAME)
    )

    if not target_session_id:
        return templates.TemplateResponse(
            request=request,
            name="finish.html",
            context={"session": None},
        )

    student_cookie = request.cookies.get(STUDENT_COOKIE_NAME)
    is_admin = is_admin_authenticated(request)

    # Security check: if student attempts to view another student's session id
    if student_cookie and student_cookie != target_session_id and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ запрещен. Вы можете просматривать только результаты своей попытки.",
        )

    session_results = await get_session_results_data(db, target_session_id)
    if not session_results:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Сессия тестирования не найдена.",
        )

    if not session_results["is_completed"]:
        return RedirectResponse(
            url=f"/exam/{target_session_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    response = templates.TemplateResponse(
        request=request,
        name="finish.html",
        context={
            "session": session_results,
            "score": session_results["score"],
            "total": session_results["total"],
            "formatted_score": session_results["formatted_score"],
            "grade": session_results["grade"],
            "grade_label": session_results["grade_label"],
            "percentage": session_results["percentage"],
            "questions": session_results["questions"],
            "correct_count": session_results["correct_count"],
            "error_count": session_results["error_count"],
        },
    )
    response.set_cookie(
        key=STUDENT_COOKIE_NAME,
        value=target_session_id,
        httponly=True,
        samesite="lax",
        max_age=86400 * 30,
    )
    return response


# ==========================================
# PROJECTOR & QR CODE
# ==========================================


@app.get("/admin/screen", response_class=HTMLResponse)
async def admin_screen(request: Request):
    """Classroom projector screen displaying instructions and large QR code."""
    base_url = str(request.base_url).rstrip("/")
    target_url = f"{base_url}/"
    return templates.TemplateResponse(
        request=request,
        name="admin_screen.html",
        context={"target_url": target_url},
    )


@app.get("/qr")
@app.get("/admin/qr")
def qr_image(
    request: Request,
    target_url: str | None = None,
    url: str | None = None,
):
    """Generates PNG QR-code on the fly.

    Accepts target_url (or url); falls back to request.base_url.
    """
    target = target_url or url or str(request.base_url)
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(target)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0f172a", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.getvalue(), media_type="image/png")


# ==========================================
# TEACHER / ADMIN ROUTES
# ==========================================


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    """Admin login page."""
    if is_admin_authenticated(request):
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={"error": None},
    )


@app.post("/admin/login")
async def admin_login_submit(
    request: Request,
    password: str = Form(...),
):
    """Handles admin password form submission."""
    if password == ADMIN_PASSWORD:
        response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            key=ADMIN_COOKIE_NAME,
            value=ADMIN_PASSWORD,
            httponly=True,
            samesite="lax",
            max_age=86400 * 7,
        )
        return response

    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={"error": "Неверный пароль администратора. Проверьте .env файл."},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


@app.get("/admin/logout")
async def admin_logout():
    """Logs out admin by clearing cookie."""
    response = RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(ADMIN_COOKIE_NAME)
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    group: str | None = None,
    sort: str = "score_desc",
    q: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    """Teacher dashboard with group filters, sorting, and mistake inspection."""
    query = """
    SELECT session_id, fio, group_name, test_date, score, total, grade, is_completed, focus_loss_count, created_at
    FROM test_sessions
    WHERE is_completed = 1
    """
    params: list[Any] = []

    if group and group in ALLOWED_GROUPS:
        query += " AND group_name = ?"
        params.append(group)

    if q and q.strip():
        query += " AND fio LIKE ?"
        params.append(f"%{q.strip()}%")

    # Sorting
    if sort == "score_asc":
        query += " ORDER BY score ASC, fio ASC"
    elif sort == "fio_asc":
        query += " ORDER BY fio ASC"
    elif sort == "fio_desc":
        query += " ORDER BY fio DESC"
    elif sort == "date_desc":
        query += " ORDER BY created_at DESC"
    else:  # default score_desc
        query += " ORDER BY score DESC, fio ASC"

    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()

    sessions: list[dict[str, Any]] = []
    grades_count = {5: 0, 4: 0, 3: 0, 2: 0}
    total_score = 0

    for r in rows:
        sc = float(r["score"])
        tot = float(r["total"])
        grd = r["grade"]
        pct = calculate_percentage(sc, tot)
        if grd in grades_count:
            grades_count[grd] += 1
        total_score += sc

        sessions.append(
            {
                "session_id": r["session_id"],
                "fio": r["fio"],
                "group_name": r["group_name"],
                "test_date": r["test_date"],
                "score": sc,
                "total": tot,
                "formatted_score": format_score(sc, tot),
                "percentage": pct,
                "grade": grd,
                "focus_loss_count": r["focus_loss_count"] or 0,
                "created_at": r["created_at"],
            }
        )

    completed_count = len(sessions)
    avg_score = round(total_score / completed_count, 1) if completed_count > 0 else 0.0

    stats = {
        "completed_count": completed_count,
        "avg_score": avg_score,
        "grades_count": grades_count,
    }

    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={
            "sessions": sessions,
            "stats": stats,
            "groups": ALLOWED_GROUPS,
            "current_group": group or "",
            "current_sort": sort,
            "search_query": q or "",
        },
    )


@app.get("/admin/session/{session_id}", response_class=HTMLResponse)
async def admin_session_detail(
    request: Request,
    session_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    """Detailed view of a student's answers, errors, and correct choices."""
    cursor = await db.execute(
        """
        SELECT session_id, fio, group_name, test_date, score, total, grade, is_completed, focus_loss_count, created_at
        FROM test_sessions
        WHERE session_id = ?;
        """,
        (session_id,),
    )
    sess_row = await cursor.fetchone()
    if not sess_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Сессия не найдена.",
        )

    cursor = await db.execute(
        """
        SELECT sq.question_order, sq.options_json, sq.selected_answer, sq.is_correct,
               q.id as question_id, q.section, q.question_text, q.correct_answer
        FROM session_questions sq
        JOIN questions q ON sq.question_id = q.id
        WHERE sq.session_id = ?
        ORDER BY sq.question_order ASC;
        """,
        (session_id,),
    )
    q_rows = await cursor.fetchall()

    questions = []
    for qr in q_rows:
        options = json.loads(qr["options_json"])
        questions.append(
            {
                "question_order": qr["question_order"],
                "question_id": qr["question_id"],
                "section": qr["section"],
                "section_name": ALLOWED_SECTIONS.get(qr["section"], ""),
                "question_text": qr["question_text"],
                "options": options,
                "selected_answer": qr["selected_answer"],
                "correct_answer": qr["correct_answer"],
                "is_correct": bool(qr["is_correct"]),
            }
        )

    sc = float(sess_row["score"])
    tot = float(sess_row["total"])
    session_dict = {
        "session_id": sess_row["session_id"],
        "fio": sess_row["fio"],
        "group_name": sess_row["group_name"],
        "test_date": sess_row["test_date"],
        "score": sc,
        "total": tot,
        "formatted_score": format_score(sc, tot),
        "grade": sess_row["grade"],
        "percentage": calculate_percentage(sc, tot),
        "focus_loss_count": sess_row["focus_loss_count"] or 0,
        "questions": questions,
    }

    return templates.TemplateResponse(
        request=request,
        name="admin_session_detail.html",
        context={"session": session_dict},
    )


@app.get("/admin/export")
async def admin_export_excel(
    group: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    """Exports test results to a styled Excel (.xlsx) spreadsheet."""
    query = """
    SELECT session_id, fio, group_name, test_date, score, total, grade, is_completed
    FROM test_sessions
    WHERE is_completed = 1
    """
    params: list[Any] = []
    if group and group in ALLOWED_GROUPS:
        query += " AND group_name = ?"
        params.append(group)

    query += " ORDER BY group_name ASC, fio ASC"

    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()

    sessions = []
    for r in rows:
        sc = float(r["score"])
        tot = float(r["total"])
        sessions.append(
            {
                "session_id": r["session_id"],
                "fio": r["fio"],
                "group_name": r["group_name"],
                "test_date": r["test_date"],
                "score": sc,
                "total": tot,
                "grade": r["grade"],
                "percentage": calculate_percentage(sc, tot),
            }
        )

    excel_stream = generate_excel_report(sessions, group_filter=group)

    group_suffix = f"_{group.replace(' ', '_')}" if group else "_all_groups"
    filename = f"vedomost_it{group_suffix}.xlsx"

    return StreamingResponse(
        excel_stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/admin/clear-results")
async def admin_clear_results(
    db: aiosqlite.Connection = Depends(get_db),
    _auth: None = Depends(require_admin),
):
    """Deletes all student test sessions and submitted answers, keeping question bank intact."""
    await db.execute("DELETE FROM session_questions;")
    await db.execute("DELETE FROM test_sessions;")
    await db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
