import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from config import settings
from models.report import (
    QAReport, StoryQAResult, CriterionResult, QAVerdict, CommitInfo,
)
from agent import git_client, goodocs_client, llm_client

logger = logging.getLogger(__name__)


async def run_qa() -> QAReport:
    """
    전체 QA 파이프라인을 실행하고 QAReport를 반환한다.

    1. 최근 커밋 수집 (모든 설정된 레포)
    2. ITSM 스토리 번호 추출
    3. goodocs에서 요구사항 조회
    4. 요구사항 체크리스트 분해 (Step 1)
    5. 항목별 코드 검증 (Step 2a) + 안티패턴 탐지 (Step 2b)
    6. 최종 판정 종합 (Step 3)
    7. 리포트 생성
    """
    run_start = datetime.now(tz=timezone.utc)
    since = run_start - timedelta(hours=settings.lookback_hours)
    qa_guide = llm_client.load_qa_guide()

    logger.info(
        f"QA run started. lookback={settings.lookback_hours}h, "
        f"qa_guide={'loaded' if qa_guide else 'not found'}"
    )

    # Step 1: 모든 레포에서 최근 커밋 수집
    all_commits: list[CommitInfo] = []
    repo_results = await asyncio.gather(
        *[git_client.fetch_recent_commits(repo, since) for repo in settings.git_repos],
        return_exceptions=True,
    )
    for repo, result in zip(settings.git_repos, repo_results):
        if isinstance(result, Exception):
            logger.error(f"Failed to fetch commits from {repo}: {result}")
        else:
            logger.info(f"Fetched {len(result)} commits from {repo}")
            all_commits.extend(result)

    # Step 2: ITSM 스토리 번호 → 커밋 매핑
    story_to_commits: dict[str, list[CommitInfo]] = defaultdict(list)
    for commit in all_commits:
        for sid in git_client.extract_itsm_ids(commit.message):
            story_to_commits[sid].append(commit)

    logger.info(
        f"Found {len(story_to_commits)} ITSM stories from {len(all_commits)} commits"
    )

    if not story_to_commits:
        return _empty_report(run_start, "분석 기간 내 ITSM 스토리가 포함된 커밋이 없습니다.")

    # Step 3: goodocs 요구사항 병렬 조회
    stories = await goodocs_client.fetch_stories(list(story_to_commits.keys()))

    # Step 4~6: 스토리별 분석 (병렬)
    qa_results: list[StoryQAResult] = await asyncio.gather(
        *[
            _analyze_story(
                story_id=sid,
                commits=story_to_commits[sid],
                story=stories.get(sid),
                qa_guide=qa_guide,
            )
            for sid in story_to_commits
        ]
    )

    # 리포트 집계
    results_for_summary = [
        {"story_id": r.story_id, "verdict": r.verdict, "reasoning": r.reasoning}
        for r in qa_results
    ]
    summary = await llm_client.generate_report_summary(results_for_summary)

    passed  = sum(1 for r in qa_results if r.verdict == QAVerdict.PASS)
    failed  = sum(1 for r in qa_results if r.verdict == QAVerdict.FAIL)
    warned  = sum(1 for r in qa_results if r.verdict == QAVerdict.WARNING)
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
    qa_guide: str,
) -> StoryQAResult:
    """단일 ITSM 스토리에 대한 3단계 분석을 수행한다."""
    if story is None:
        logger.warning(f"Story {story_id} not found in goodocs")
        return StoryQAResult(
            story_id=story_id,
            story_title="요구사항 조회 실패",
            commits=commits,
            verdict=QAVerdict.SKIP,
            reasoning="goodocs에서 요구사항을 조회할 수 없습니다.",
            code_match_score=0.0,
            issues=["goodocs API 조회 실패"],
        )

    combined_diff = _merge_diffs(commits)

    # Step 1: 요구사항을 체크리스트로 분해
    raw_criteria = await llm_client.decompose_requirements(
        story_id=story_id,
        story_description=story.description,
        acceptance_criteria=story.acceptance_criteria,
    )
    logger.info(f"{story_id}: decomposed into {len(raw_criteria)} criteria")

    # Step 2a: 항목별 코드 검증 (병렬)
    # Step 2b: 안티패턴 탐지 (병렬)
    criterion_tasks = [
        llm_client.verify_criterion_in_code(
            story_id=story_id,
            criterion=c.get("criterion", ""),
            criterion_type=c.get("type", "기타"),
            diff_text=combined_diff,
            qa_guide=qa_guide,
        )
        for c in raw_criteria
    ]
    antipattern_task = llm_client.analyze_antipatterns(
        story_id=story_id,
        diff_text=combined_diff,
        qa_guide=qa_guide,
    )

    criterion_raw_results, antipattern_result = await asyncio.gather(
        asyncio.gather(*criterion_tasks) if criterion_tasks else asyncio.coroutine(lambda: [])(),
        antipattern_task,
    )

    # CriterionResult 모델로 변환
    criteria_results: list[CriterionResult] = []
    criteria_for_verdict: list[dict] = []
    for raw_c, raw_r in zip(raw_criteria, criterion_raw_results):
        verdict_str = raw_r.get("verdict", "WARNING")
        try:
            v = QAVerdict(verdict_str)
        except ValueError:
            v = QAVerdict.WARNING

        criteria_results.append(
            CriterionResult(
                criterion=raw_c.get("criterion", ""),
                verdict=v,
                evidence=raw_r.get("evidence", ""),
                file_path=raw_r.get("file_path", ""),
                line_hint=raw_r.get("line_hint", ""),
            )
        )
        criteria_for_verdict.append({
            "criterion": raw_c.get("criterion", ""),
            "verdict": verdict_str,
            "evidence": raw_r.get("evidence", ""),
        })

    antipattern_issues = antipattern_result.get("issues", [])
    suggestions = antipattern_result.get("suggestions", [])

    # Step 3: 최종 판정
    final = await llm_client.build_final_verdict(
        story_id=story_id,
        story_title=story.title,
        criteria_verdicts=criteria_for_verdict,
        antipattern_issues=antipattern_issues,
    )

    try:
        verdict = QAVerdict(final.get("verdict", "WARNING"))
    except ValueError:
        verdict = QAVerdict.WARNING

    return StoryQAResult(
        story_id=story_id,
        story_title=story.title,
        commits=commits,
        verdict=verdict,
        reasoning=final.get("reasoning", ""),
        code_match_score=float(final.get("score", 0.0)),
        criteria_results=criteria_results,
        issues=antipattern_issues,
        suggestions=suggestions,
    )


def _merge_diffs(commits: list[CommitInfo]) -> str:
    parts = []
    for c in commits:
        if c.diff_summary:
            parts.append(
                f"# Commit: {c.commit_hash} by {c.author}\n"
                f"# {c.message[:100]}\n"
                f"{c.diff_summary}"
            )
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
