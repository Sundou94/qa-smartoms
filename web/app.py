from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pathlib import Path
import logging

from config import settings
from storage import db

logger = logging.getLogger(__name__)

app = FastAPI(title="QA SmartOMS Dashboard", version="1.0.0")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# ── 리포트 라우트 ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    reports = db.list_reports(limit=30)
    latest = db.get_latest_report()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "reports": reports, "latest": latest},
    )


@app.get("/reports/{report_id}", response_class=HTMLResponse)
async def report_detail(request: Request, report_id: str):
    report = db.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="리포트를 찾을 수 없습니다.")
    return templates.TemplateResponse(
        "report_detail.html",
        {"request": request, "report": report},
    )


# ── QA 가이드 라우트 ───────────────────────────────────────────

@app.get("/qa-guide", response_class=HTMLResponse)
async def qa_guide_page(request: Request):
    content = _read_guide()
    return templates.TemplateResponse(
        "qa_guide.html",
        {"request": request, "content": content},
    )


class GuideUpdateRequest(BaseModel):
    content: str


@app.get("/api/qa-guide")
async def api_get_guide():
    return {"content": _read_guide()}


@app.put("/api/qa-guide")
async def api_update_guide(body: GuideUpdateRequest):
    try:
        path = Path(settings.qa_guide_path)
        path.write_text(body.content, encoding="utf-8")
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Failed to save QA guide: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── JSON API ───────────────────────────────────────────────────

@app.get("/api/reports")
async def api_list_reports():
    return db.list_reports(limit=30)


@app.get("/api/reports/{report_id}")
async def api_get_report(report_id: str):
    report = db.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404)
    return report.model_dump(mode="json")


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── 내부 헬퍼 ─────────────────────────────────────────────────

def _read_guide() -> str:
    path = Path(settings.qa_guide_path)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "# QA 가이드\n\n내용을 작성하세요."
