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
    "Группа 1",
    "Группа 2",
    "Группа 3",
    "Группа 4",
    "Группа 5",
    "Группа 6",
]


class QuestionCreate(BaseModel):
    section: int
    question_text: str
    correct_answer: str
    distractor1: str
    distractor2: str
    distractor3: str


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
    is_completed: bool
    created_at: Any
    questions: list[dict[str, Any]] = []
