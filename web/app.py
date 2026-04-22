from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import logging

from storage import db

logger = logging.getLogger(__name__)

app = FastAPI(title="QA SmartOMS Dashboard", version="1.0.0")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


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
