import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from config import settings
from models.report import QAReport, StoryQAResult, QAVerdict, CommitInfo
from agent import git_client, goodocs_client, llm_client, oracle_client

logger = logging.getLogger(__name__)


async def run_qa() -> QAReport:
    """
    전체 QA 파이프라인을 실행하고 QAReport를 반환한다.

    1. 최근 커밋 수집 (모든 설정된 레포)
    2. ITSM 스토리 번호 추출
    3. goodocs에서 요구사항 조회
    4. LLM으로 코드 vs 요구사항 비교
    5. Oracle DB 검증
    6. 리포트 생성
    """
    run_start = datetime.now(tz=timezone.utc)
    since = run_start - timedelta(hours=settings.lookback_hours)

    logger.info(f"QA run started. Looking back {settings.lookback_hours}h from {run_start.isoformat()}")

    # Step 1: 모든 레포에서 최근 커밋 수집
    all_commits: list[CommitInfo] = []
    repo_tasks = [git_client.fetch_recent_commits(repo, since) for repo in settings.git_repos]
    repo_results = await asyncio.gather(*repo_tasks, return_exceptions=True)

    for repo, result in zip(settings.git_repos, repo_results):
        if isinstance(result, Exception):
            logger.error(f"Failed to fetch commits from {repo}: {result}")
        elif isinstance(result, list):
            logger.info(f"Fetched {len(result)} commits from {repo}")
            all_commits.extend(result)

    if not all_commits:
        logger.warning("No commits found in the lookback period.")

    # Step 2: ITSM 스토리 번호 → 커밋 매핑
    story_to_commits: dict[str, list[CommitInfo]] = defaultdict(list)
    for commit in all_commits:
        itsm_ids = git_client.extract_itsm_ids(commit.message)
        if not itsm_ids:
            logger.debug(f"No ITSM ID in commit: {commit.commit_hash} - {commit.message[:60]}")
        for sid in itsm_ids:
            story_to_commits[sid].append(commit)

    logger.info(f"Found {len(story_to_commits)} unique ITSM stories from {len(all_commits)} commits")

    if not story_to_commits:
        return _empty_report(run_start, "분석 기간 내 ITSM 스토리가 포함된 커밋이 없습니다.")

    # Step 3: goodocs에서 요구사항 병렬 조회
    story_ids = list(story_to_commits.keys())
    stories = await goodocs_client.fetch_stories(story_ids)

    # Step 4 & 5: 각 스토리별 LLM 분석 + Oracle 검증
    story_tasks = [
        _analyze_story(
            story_id=sid,
            commits=story_to_commits[sid],
            story=stories.get(sid),
        )
        for sid in story_ids
    ]
    qa_results: list[StoryQAResult] = await asyncio.gather(*story_tasks)

    # Step 6: 요약 생성
    results_for_summary = [
        {"story_id": r.story_id, "verdict": r.verdict, "reasoning": r.reasoning}
        for r in qa_results
    ]
    summary = await llm_client.generate_report_summary(results_for_summary)

    passed = sum(1 for r in qa_results if r.verdict == QAVerdict.PASS)
    failed = sum(1 for r in qa_results if r.verdict == QAVerdict.FAIL)
    warned = sum(1 for r in qa_results if r.verdict == QAVerdict.WARNING)
    skipped = sum(1 for r in qa_results if r.verdict == QAVerdict.SKIP)

    report = QAReport(
        report_id=str(uuid.uuid4()),
        run_date=run_start,
        total_stories=len(qa_results),
        passed=passed,
        failed=failed,
        warned=warned,
        skipped=skipped,
        results=qa_results,
        summary=summary,
    )

    logger.info(
        f"QA run complete. PASS={passed}, FAIL={failed}, WARNING={warned}, SKIP={skipped}"
    )
    return report


async def _analyze_story(
    story_id: str,
    commits: list[CommitInfo],
    story,
) -> StoryQAResult:
    """단일 ITSM 스토리에 대한 분석을 수행한다."""
    if story is None:
        logger.warning(f"Story {story_id} not found in goodocs, skipping")
        return StoryQAResult(
            story_id=story_id,
            story_title="요구사항 조회 실패",
            commits=commits,
            verdict=QAVerdict.SKIP,
            reasoning="goodocs에서 요구사항을 조회할 수 없습니다.",
            code_match_score=0.0,
            issues=["goodocs API 조회 실패"],
        )

    # 모든 커밋의 diff를 합산 (레포별로 그룹화)
    combined_diff = _merge_diffs(commits)
    primary_repo = commits[0].repo if commits else ""

    # LLM 분석
    llm_result = await llm_client.analyze_code_vs_requirements(
        story_id=story_id,
        story_description=story.description,
        acceptance_criteria=story.acceptance_criteria,
        code_diff=combined_diff,
        repo_name=primary_repo,
    )

    verdict_str = llm_result.get("verdict", "WARNING")
    try:
        verdict = QAVerdict(verdict_str)
    except ValueError:
        verdict = QAVerdict.WARNING

    # Oracle 검증 (LLM이 제안한 항목들)
    oracle_checks = llm_result.get("oracle_checks_needed", [])
    oracle_results = []

    if oracle_checks:
        oracle_tasks = []
        for check_desc in oracle_checks[:3]:  # 최대 3개 쿼리로 제한
            oracle_tasks.append(
                _run_oracle_check(story_id, story.description, check_desc)
            )
        oracle_results = await asyncio.gather(*oracle_tasks, return_exceptions=True)
        oracle_results = [r for r in oracle_results if not isinstance(r, Exception)]

        # Oracle FAIL이 있으면 전체 verdict 강등
        if any(r.verdict == QAVerdict.FAIL for r in oracle_results):
            if verdict == QAVerdict.PASS:
                verdict = QAVerdict.WARNING

    return StoryQAResult(
        story_id=story_id,
        story_title=story.title,
        commits=commits,
        verdict=verdict,
        reasoning=llm_result.get("reasoning", ""),
        code_match_score=float(llm_result.get("score", 0.0)),
        issues=llm_result.get("issues", []),
        suggestions=llm_result.get("suggestions", []),
        oracle_validations=oracle_results,
    )


async def _run_oracle_check(story_id: str, story_desc: str, check_desc: str):
    """Oracle 검증 쿼리를 생성하고 실행한다."""
    gen = await llm_client.generate_oracle_validation_query(
        story_id=story_id,
        story_description=story_desc,
        check_description=check_desc,
    )
    return await oracle_client.run_validation_query(
        query=gen.get("query", ""),
        description=gen.get("description", check_desc),
        expected_empty=gen.get("expected_empty", True),
    )


def _merge_diffs(commits: list[CommitInfo]) -> str:
    """여러 커밋의 diff를 하나의 텍스트로 합산한다."""
    parts = []
    for c in commits:
        if c.diff_summary:
            parts.append(f"# Commit: {c.commit_hash} by {c.author}\n# {c.message[:100]}\n{c.diff_summary}")
    return "\n\n".join(parts)


def _empty_report(run_date: datetime, summary: str) -> QAReport:
    return QAReport(
        report_id=str(uuid.uuid4()),
        run_date=run_date,
        total_stories=0,
        passed=0,
        failed=0,
        warned=0,
        skipped=0,
        results=[],
        summary=summary,
    )
