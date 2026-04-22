import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

from config import settings
from models.report import (
    QAReport, StoryQAResult, CriterionResult, QAVerdict, CommitInfo,
)

logger = logging.getLogger(__name__)

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        db_path = Path(settings.report_db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(db_path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _init_schema(_conn)
    return _conn


def _init_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS qa_reports (
            report_id     TEXT PRIMARY KEY,
            run_date      TEXT NOT NULL,
            total_stories INTEGER,
            passed        INTEGER,
            failed        INTEGER,
            warned        INTEGER,
            skipped       INTEGER,
            summary       TEXT,
            created_at    TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS story_results (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id        TEXT NOT NULL REFERENCES qa_reports(report_id),
            story_id         TEXT NOT NULL,
            story_title      TEXT,
            verdict          TEXT,
            reasoning        TEXT,
            code_match_score REAL,
            criteria_json    TEXT,
            issues_json      TEXT,
            suggestions_json TEXT,
            commits_json     TEXT,
            analyzed_at      TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_story_report ON story_results(report_id);
        CREATE INDEX IF NOT EXISTS idx_report_date  ON qa_reports(run_date);
    """)
    conn.commit()


def save_report(report: QAReport) -> None:
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO qa_reports
            (report_id, run_date, total_stories, passed, failed, warned, skipped, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.report_id,
                report.run_date.isoformat(),
                report.total_stories,
                report.passed,
                report.failed,
                report.warned,
                report.skipped,
                report.summary,
            ),
        )

        for result in report.results:
            conn.execute(
                """
                INSERT INTO story_results
                (report_id, story_id, story_title, verdict, reasoning, code_match_score,
                 criteria_json, issues_json, suggestions_json, commits_json, analyzed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.report_id,
                    result.story_id,
                    result.story_title,
                    result.verdict.value,
                    result.reasoning,
                    result.code_match_score,
                    json.dumps(
                        [c.model_dump(mode="json") for c in result.criteria_results],
                        ensure_ascii=False,
                    ),
                    json.dumps(result.issues, ensure_ascii=False),
                    json.dumps(result.suggestions, ensure_ascii=False),
                    json.dumps(
                        [c.model_dump(mode="json") for c in result.commits],
                        ensure_ascii=False,
                        default=str,
                    ),
                    result.analyzed_at.isoformat(),
                ),
            )

        conn.commit()
        logger.info(f"Report {report.report_id} saved")
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to save report: {e}")
        raise


def list_reports(limit: int = 30) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM qa_reports ORDER BY run_date DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_report(report_id: str) -> QAReport | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM qa_reports WHERE report_id = ?", (report_id,)
    ).fetchone()
    if not row:
        return None

    story_rows = conn.execute(
        "SELECT * FROM story_results WHERE report_id = ? ORDER BY id",
        (report_id,),
    ).fetchall()

    results = []
    for sr in story_rows:
        criteria_raw = json.loads(sr["criteria_json"] or "[]")
        commits_raw  = json.loads(sr["commits_json"]  or "[]")
        results.append(
            StoryQAResult(
                story_id=sr["story_id"],
                story_title=sr["story_title"] or "",
                verdict=QAVerdict(sr["verdict"]),
                reasoning=sr["reasoning"] or "",
                code_match_score=sr["code_match_score"] or 0.0,
                criteria_results=[CriterionResult(**c) for c in criteria_raw],
                issues=json.loads(sr["issues_json"] or "[]"),
                suggestions=json.loads(sr["suggestions_json"] or "[]"),
                commits=[CommitInfo(**c) for c in commits_raw],
                analyzed_at=datetime.fromisoformat(sr["analyzed_at"]),
            )
        )

    return QAReport(
        report_id=row["report_id"],
        run_date=datetime.fromisoformat(row["run_date"]),
        total_stories=row["total_stories"],
        passed=row["passed"],
        failed=row["failed"],
        warned=row["warned"],
        skipped=row["skipped"],
        summary=row["summary"] or "",
        results=results,
    )


def get_latest_report() -> QAReport | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT report_id FROM qa_reports ORDER BY run_date DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return get_report(row["report_id"])
