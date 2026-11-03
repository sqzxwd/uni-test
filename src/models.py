from typing import Any

from pydantic import BaseModel, Field

# 6 разрешенных разделов из банка ПИМ
ALLOWED_SECTIONS: dict[int, str] = {
    1: "Введение в ИТ",
    2: "Информация и ее обработка",
    3: "Аппаратное обеспечение",
    4: "Программное обеспечение",
    7: "Сети и интернет",
    8: "Информационная безопасность",
}

# Распределение вопросов по билету (всего 20)
SECTION_DISTRIBUTION: dict[int, int] = {
    1: 3,  # 3 вопроса
    2: 4,  # 4 вопроса
    3: 4,  # 4 вопроса
    4: 3,  # 3 вопроса
    7: 3,  # 3 вопроса
    8: 3,  # 3 вопроса
}

ALLOWED_GROUPS: list[str] = [
    "161-11",
    "161-32",
    "161-51",
    "161-54",
    "161-55",
    "161-61",
    "161-62",
    "161-65",
    "161-71",
    "161-75",
    "161-81",
    "161-84",
    "161-85",
]


class QuestionCreate(BaseModel):
    section: int
    question_text: str
    correct_answer: str
    distractor1: str
    distractor2: str
    distractor3: str
    explanation: str | None = None


class QuestionRecord(QuestionCreate):
    id: int


class StudentRegistration(BaseModel):
    fio: str = Field(..., min_length=3, max_length=150)
    group_name: str = Field(...)
    test_date: str = Field(...)


class ExamQuestionItem(BaseModel):
    question_id: int
    question_order: int
    section: int
    section_name: str
    question_text: str
    options: list[str]
    selected_answer: str | None = None


class QuestionResultItem(BaseModel):
    question_id: int
    question_order: int
    section: int
    section_name: str
    question_text: str
    options: list[str]
    selected_answer: str | None = None
    correct_answer: str
    is_correct: bool
    correctness: str  # "correct", "incorrect", "partial", "unanswered"
    correctness_label: str  # "Правильно", "Неправильно", "Частично", "Нет ответа"
    points: float
    explanation: str | None = None


class TestSessionDetail(BaseModel):
    session_id: str
    fio: str
    group_name: str
    test_date: str
    score: float = 0.0
    total: float = 10.0
    grade: int
    grade_label: str
    percentage: float
    focus_loss_count: int = 0
    is_completed: bool
    created_at: Any
    questions: list[dict[str, Any]] = []


class TestResultDetail(BaseModel):
    session_id: str
    fio: str
    group_name: str
    test_date: str
    score: float = 0.0
    total: float = 10.0
    formatted_score: str
    percentage: float
    grade: int
    grade_label: str
    focus_loss_count: int = 0
    is_completed: bool
    created_at: Any
    correct_count: int = 0
    error_count: int = 0
    questions: list[QuestionResultItem] = []
