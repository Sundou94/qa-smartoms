import json
import logging
from pathlib import Path
from openai import AsyncOpenAI
from config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
    return _client


def load_qa_guide() -> str:
    """QA 가이드 MD 파일을 로드한다. 없으면 빈 문자열 반환."""
    path = Path(settings.qa_guide_path)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def _file_sections(diff_text: str, max_chars: int = 12000) -> str:
    """diff 텍스트를 max_chars 이내로 잘라 파일 경로 목록과 함께 반환한다."""
    if len(diff_text) <= max_chars:
        return diff_text

    # 파일 경로 목록을 앞에 붙이고 diff를 잘라낸다
    files = [line[4:] for line in diff_text.splitlines() if line.startswith("+++ b/")]
    header = "변경된 파일 목록:\n" + "\n".join(f"  - {f}" for f in files) + "\n\n"
    return header + diff_text[:max_chars - len(header)] + "\n... (diff 생략됨)"


async def decompose_requirements(
    story_id: str,
    story_description: str,
    acceptance_criteria: str,
) -> list[dict]:
    """
    Step 1: 요구사항을 검증 가능한 체크리스트 항목으로 분해한다.
    Returns: [{"criterion": "...", "type": "기능|보안|성능|UX"}]
    """
    prompt = f"""다음 ITSM 요구사항을 코드 검토 시 검증 가능한 구체적인 체크리스트 항목으로 분해하세요.

## ITSM 스토리: {story_id}

### 요구사항
{story_description}

### 인수 조건
{acceptance_criteria if acceptance_criteria else "별도 인수 조건 없음"}

## 응답 형식 (JSON 배열만 반환)
[
  {{
    "criterion": "검증 항목을 한 문장으로 명확하게 기술 (예: '사용자 ID 중복 체크 로직이 구현되어야 한다')",
    "type": "기능 | 보안 | 성능 | 데이터 | 예외처리 | 기타"
  }}
]

주의:
- 반드시 코드에서 확인 가능한 항목만 포함
- 최소 2개, 최대 8개
- 모호한 항목(예: '잘 동작해야 한다') 금지
"""
    client = get_client()
    try:
        resp = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        raw = json.loads(resp.choices[0].message.content)
        # 모델이 {"items": [...]} 또는 [...] 형태로 반환할 수 있음
        if isinstance(raw, list):
            return raw
        for key in ("items", "criteria", "checklist", "list"):
            if key in raw and isinstance(raw[key], list):
                return raw[key]
        return []
    except Exception as e:
        logger.warning(f"Requirement decomposition failed for {story_id}: {e}")
        return []


async def verify_criterion_in_code(
    story_id: str,
    criterion: str,
    criterion_type: str,
    diff_text: str,
    qa_guide: str,
) -> dict:
    """
    Step 2a: 개별 요구사항 항목이 코드에 구현되었는지 검증한다.
    Returns: {"verdict": "PASS|FAIL|WARNING", "evidence": "...", "file_path": "...", "line_hint": "..."}
    """
    guide_section = f"\n\n## QA 가이드 (컨벤션 및 안티패턴)\n{qa_guide}" if qa_guide else ""

    prompt = f"""당신은 코드 리뷰 전문가입니다. 아래 요구사항 항목이 코드 변경사항에 올바르게 구현되었는지 판단하세요.
{guide_section}

## 검증 대상
- 스토리: {story_id}
- 항목 유형: {criterion_type}
- 검증 항목: {criterion}

## 코드 변경사항 (diff)
```diff
{_file_sections(diff_text)}
```

## 응답 형식 (JSON만 반환)
{{
  "verdict": "PASS" | "FAIL" | "WARNING",
  "evidence": "코드에서 발견한 구현 근거 또는 미구현 근거를 1~2문장으로",
  "file_path": "관련 파일 경로 (없으면 빈 문자열)",
  "line_hint": "관련 함수명 또는 라인 정보 (없으면 빈 문자열)"
}}

판정 기준:
- PASS: 요구사항이 코드에 명확히 구현됨
- WARNING: 부분 구현 또는 판단 불확실
- FAIL: 구현 없거나 잘못 구현됨
"""
    client = get_client()
    try:
        resp = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        logger.warning(f"Criterion verification failed ({criterion[:40]}): {e}")
        return {
            "verdict": "WARNING",
            "evidence": f"LLM 분석 오류: {e}",
            "file_path": "",
            "line_hint": "",
        }


async def analyze_antipatterns(
    story_id: str,
    diff_text: str,
    qa_guide: str,
) -> dict:
    """
    Step 2b: QA 가이드 기반으로 안티패턴 및 컨벤션 위반을 탐지한다.
    Returns: {"issues": [...], "suggestions": [...]}
    """
    if not qa_guide:
        return {"issues": [], "suggestions": []}

    prompt = f"""당신은 코드 품질 전문가입니다. QA 가이드를 기준으로 코드 변경사항에서 위반 사항을 탐지하세요.

## QA 가이드 (컨벤션 및 안티패턴 기준)
{qa_guide}

## 코드 변경사항 (diff) — 스토리 {story_id}
```diff
{_file_sections(diff_text, max_chars=10000)}
```

## 응답 형식 (JSON만 반환)
{{
  "issues": [
    "위반 항목: [파일명/함수명] 구체적인 문제 설명"
  ],
  "suggestions": [
    "개선 방법 설명"
  ]
}}

주의:
- QA 가이드에 명시된 규칙만 기준으로 판단
- 위반이 없으면 빈 배열
- 각 항목은 구체적으로 (파일명, 함수명 포함)
"""
    client = get_client()
    try:
        resp = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        logger.warning(f"Anti-pattern analysis failed for {story_id}: {e}")
        return {"issues": [], "suggestions": []}


async def build_final_verdict(
    story_id: str,
    story_title: str,
    criteria_verdicts: list[dict],
    antipattern_issues: list[str],
) -> dict:
    """
    Step 3: 개별 기준 결과를 종합하여 최종 판정과 요약 근거를 생성한다.
    Returns: {"verdict": "PASS|FAIL|WARNING|SKIP", "score": 0.0~1.0, "reasoning": "..."}
    """
    total = len(criteria_verdicts)
    if total == 0:
        return {
            "verdict": "SKIP",
            "score": 0.0,
            "reasoning": "검증 가능한 요구사항 항목을 추출할 수 없었습니다.",
        }

    pass_count = sum(1 for c in criteria_verdicts if c.get("verdict") == "PASS")
    fail_count = sum(1 for c in criteria_verdicts if c.get("verdict") == "FAIL")
    warn_count = sum(1 for c in criteria_verdicts if c.get("verdict") == "WARNING")
    score = round(pass_count / total, 2)

    if fail_count > 0:
        verdict = "FAIL"
    elif warn_count > 0 or antipattern_issues:
        verdict = "WARNING"
    else:
        verdict = "PASS"

    criteria_summary = "\n".join(
        f"- [{c.get('verdict')}] {c.get('criterion', '')}: {c.get('evidence', '')[:80]}"
        for c in criteria_verdicts
    )
    issues_summary = "\n".join(f"- {i}" for i in antipattern_issues[:5]) if antipattern_issues else "없음"

    prompt = f"""QA 검증 결과를 바탕으로 개발팀을 위한 한국어 요약 근거를 2~3문장으로 작성하세요.

스토리: {story_id} — {story_title}
전체 판정: {verdict} (통과율 {int(score*100)}%)

항목별 결과:
{criteria_summary}

안티패턴/컨벤션 이슈:
{issues_summary}

요약 근거만 텍스트로 반환하세요 (JSON 아님).
"""
    client = get_client()
    try:
        resp = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        reasoning = resp.choices[0].message.content.strip()
    except Exception as e:
        reasoning = f"PASS {pass_count}건, FAIL {fail_count}건, WARNING {warn_count}건. (요약 생성 오류: {e})"

    return {"verdict": verdict, "score": score, "reasoning": reasoning}


async def generate_report_summary(results: list) -> str:
    """전체 QA 결과를 요약하는 한국어 리포트를 생성한다."""
    pass_count = sum(1 for r in results if r.get("verdict") == "PASS")
    fail_count = sum(1 for r in results if r.get("verdict") == "FAIL")
    warn_count = sum(1 for r in results if r.get("verdict") == "WARNING")

    failed = [f"- {r['story_id']}: {r['reasoning'][:80]}" for r in results if r.get("verdict") == "FAIL"]
    warned = [f"- {r['story_id']}: {r['reasoning'][:80]}" for r in results if r.get("verdict") == "WARNING"]

    prompt = f"""오늘 밤 QA 에이전트의 코드-요구사항 정합성 검증 결과를 3~5문장으로 요약하세요.
개발팀이 내일 아침 확인해야 할 핵심 사항을 중심으로 작성합니다.

전체: {len(results)}건 | PASS: {pass_count} | WARNING: {warn_count} | FAIL: {fail_count}

실패 항목:
{chr(10).join(failed) if failed else "없음"}

경고 항목:
{chr(10).join(warned) if warned else "없음"}
"""
    client = get_client()
    try:
        resp = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Summary generation failed: {e}")
        return f"QA 검증 완료. PASS: {pass_count}, WARNING: {warn_count}, FAIL: {fail_count}"
