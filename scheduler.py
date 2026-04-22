import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings

logger = logging.getLogger(__name__)


async def _run_qa_job():
    """스케줄러가 호출하는 QA 실행 함수."""
    from agent import qa_agent
    from storage import db

    logger.info("Scheduled QA job started")
    try:
        report = await qa_agent.run_qa()
        db.save_report(report)
        logger.info(
            f"QA job complete: report_id={report.report_id}, "
            f"PASS={report.passed}, FAIL={report.failed}, WARNING={report.warned}"
        )
    except Exception as e:
        logger.exception(f"QA job failed: {e}")


def build_scheduler() -> AsyncIOScheduler:
    """Cron 스케줄러를 생성하고 반환한다."""
    scheduler = AsyncIOScheduler()

    # "0 22 * * 1-5" 형식의 cron 표현식 파싱
    parts = settings.cron_schedule.split()
    if len(parts) != 5:
        raise ValueError(f"Invalid CRON_SCHEDULE: '{settings.cron_schedule}'. Expected 5-field cron expression.")

    minute, hour, day, month, day_of_week = parts

    scheduler.add_job(
        _run_qa_job,
        trigger=CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            timezone="Asia/Seoul",
        ),
        id="nightly_qa",
        name="야간 QA 자동 검증",
        replace_existing=True,
        misfire_grace_time=3600,  # 1시간 이내 실행 지연 허용
    )

    return scheduler
