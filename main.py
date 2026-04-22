#!/usr/bin/env python3
"""
QA SmartOMS Agent
실행:
  python main.py            - 웹서버 + 스케줄러 동시 실행 (운영 모드)
  python main.py --run-now  - QA 즉시 한 번 실행 후 종료 (테스트/수동 실행)
"""
import argparse
import asyncio
import logging
import sys

import uvicorn
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


async def run_once():
    """QA를 즉시 한 번 실행하고 리포트를 저장한다."""
    from agent import qa_agent
    from storage import db

    logger.info("Manual QA run triggered")
    report = await qa_agent.run_qa()
    db.save_report(report)
    logger.info(f"Report saved: {report.report_id}")
    logger.info(f"  PASS={report.passed}, FAIL={report.failed}, WARNING={report.warned}, SKIP={report.skipped}")
    logger.info(f"  Summary: {report.summary[:200]}")
    return report


async def run_server():
    """웹 대시보드 서버와 스케줄러를 함께 실행한다."""
    from config import settings
    from scheduler import build_scheduler
    from web.app import app

    scheduler = build_scheduler()
    scheduler.start()
    logger.info(f"Scheduler started. Next run: {scheduler.get_jobs()[0].next_run_time}")

    config = uvicorn.Config(
        app=app,
        host=settings.web_host,
        port=settings.web_port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    try:
        await server.serve()
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


def main():
    parser = argparse.ArgumentParser(description="QA SmartOMS Agent")
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="QA를 즉시 한 번 실행하고 종료합니다 (웹서버 미실행)",
    )
    args = parser.parse_args()

    if args.run_now:
        asyncio.run(run_once())
    else:
        asyncio.run(run_server())


if __name__ == "__main__":
    main()
