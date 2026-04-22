from pydantic import BaseModel
from typing import List
from datetime import datetime
from enum import Enum


class QAVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"
    SKIP = "SKIP"


class CommitInfo(BaseModel):
    commit_hash: str
    author: str
    message: str
    timestamp: datetime
    repo: str
    diff_summary: str = ""


class ITSMStory(BaseModel):
    story_id: str
    title: str
    description: str
    acceptance_criteria: str = ""
    raw: dict = {}


class CriterionResult(BaseModel):
    """요구사항 항목 하나에 대한 개별 검증 결과."""
    criterion: str
    verdict: QAVerdict
    evidence: str = ""       # 코드에서 발견한 구현 근거
    file_path: str = ""      # 관련 파일 경로
    line_hint: str = ""      # 관련 라인/함수명 힌트


class StoryQAResult(BaseModel):
    story_id: str
    story_title: str
    commits: List[CommitInfo]
    verdict: QAVerdict
    reasoning: str
    code_match_score: float  # 0.0 ~ 1.0
    criteria_results: List[CriterionResult] = []
    issues: List[str] = []
    suggestions: List[str] = []
    analyzed_at: datetime = datetime.now()


class QAReport(BaseModel):
    report_id: str
    run_date: datetime
    total_stories: int
    passed: int
    failed: int
    warned: int
    skipped: int
    results: List[StoryQAResult]
    summary: str = ""

    @property
    def pass_rate(self) -> float:
        if self.total_stories == 0:
            return 0.0
        return round(self.passed / self.total_stories * 100, 1)
