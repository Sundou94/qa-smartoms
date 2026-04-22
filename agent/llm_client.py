import json
import logging
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


async def analyze_code_vs_requirements(
    story_id: str,
    story_description: str,
    acceptance_criteria: str,
    code_diff: str,
    repo_name: str,
) -> dict:
    """
    LLM이 요구사항과 코드 변경사항을 비교하여 QA 결과를 반환한다.
    Returns: {"verdict": "PASS|FAIL|WARNING", "score": 0.0~1.0, "reasoning": "...", "issues": [...], "suggestions": [...]}
    """
    prompt = f"""당신은 시스템 QA 엔지니어입니다. ITSM 요구사항과 실제 코드 변경사항을 비교하여 정합성을 검증합니다.

## ITSM 스토리: {story_id}

### 요구사항 설명
{story_description}

### 인수 조건 (Acceptance Criteria)
{acceptance_criteria if acceptance_criteria else "명시된 인수 조건 없음"}

### 코드 변경사항 (Repository: {repo_name})
```diff
{code_diff[:8000]}
```

## 검증 지침
1. 요구사항이 코드에 올바르게 구현되었는지 판단합니다.
2. 누락된 로직, 잘못된 구현, 부분 구현을 식별합니다.
3. 비즈니스 로직의 정확성을 평가합니다.

## 응답 형식 (반드시 JSON으로만 응답)
{{
  "verdict": "PASS" | "FAIL" | "WARNING",
  "score": 0.0 ~ 1.0,
  "reasoning": "판정 이유를 2~3문장으로 설명",
  "issues": ["발견된 문제점 목록"],
  "suggestions": ["개선 제안 목록"],
  "oracle_checks_needed": ["Oracle DB에서 검증이 필요한 항목 설명 목록 (없으면 빈 배열)"]
}}

판정 기준:
- PASS: 요구사항이 코드에 완전히 구현됨 (score >= 0.8)
- WARNING: 일부 구현되었으나 미비한 부분 존재 (score 0.5 ~ 0.79)
- FAIL: 요구사항과 코드가 불일치하거나 구현 누락 (score < 0.5)
"""

    client = get_client()
    try:
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        return json.loads(content)
    except Exception as e:
        logger.error(f"LLM analysis failed for {story_id}: {e}")
        return {
            "verdict": "WARNING",
            "score": 0.0,
            "reasoning": f"LLM 분석 중 오류 발생: {e}",
            "issues": ["LLM 분석 실패"],
            "suggestions": [],
            "oracle_checks_needed": [],
        }


async def generate_oracle_validation_query(
    story_id: str,
    story_description: str,
    check_description: str,
    schema_hint: str = "",
) -> dict:
    """
    LLM이 Oracle DB 검증 쿼리를 자동 생성한다.
    Returns: {"query": "SELECT ...", "description": "...", "expected_empty": bool}
    """
    prompt = f"""당신은 Oracle SQL 전문가입니다. 비즈니스 요구사항에 맞는 데이터 정합성 검증 쿼리를 작성합니다.

## ITSM 스토리: {story_id}
{story_description}

## 검증 항목
{check_description}

{f"## 스키마 힌트{chr(10)}{schema_hint}" if schema_hint else ""}

## 응답 형식 (반드시 JSON으로만 응답)
{{
  "query": "Oracle SQL SELECT 쿼리 (데이터 이상을 찾는 쿼리, ROWNUM <= 100 제한 포함)",
  "description": "이 쿼리가 무엇을 검증하는지 한 문장 설명",
  "expected_empty": true
}}

주의:
- expected_empty가 true이면 결과가 0건일 때 PASS (이상 데이터가 없음)
- DML(INSERT/UPDATE/DELETE) 절대 작성 금지
- Oracle 문법 사용 (ROWNUM, NVL, DECODE 등)
"""

    client = get_client()
    try:
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"Oracle query generation failed for {story_id}: {e}")
        return {"query": "", "description": check_description, "expected_empty": True}


async def generate_report_summary(results: list) -> str:
    """전체 QA 결과를 요약하는 한국어 리포트를 생성한다."""
    pass_count = sum(1 for r in results if r.get("verdict") == "PASS")
    fail_count = sum(1 for r in results if r.get("verdict") == "FAIL")
    warn_count = sum(1 for r in results if r.get("verdict") == "WARNING")

    failed_stories = [
        f"- {r['story_id']}: {r['reasoning']}"
        for r in results
        if r.get("verdict") == "FAIL"
    ]
    warn_stories = [
        f"- {r['story_id']}: {r['reasoning']}"
        for r in results
        if r.get("verdict") == "WARNING"
    ]

    prompt = f"""오늘 밤 QA 에이전트가 자동으로 수행한 코드-요구사항 정합성 검증 결과를 요약해 주세요.

전체 스토리 수: {len(results)}
통과(PASS): {pass_count}
경고(WARNING): {warn_count}
실패(FAIL): {fail_count}

실패 항목:
{chr(10).join(failed_stories) if failed_stories else "없음"}

경고 항목:
{chr(10).join(warn_stories) if warn_stories else "없음"}

3~5문장의 한국어 요약 리포트를 작성해 주세요.
주요 이슈와 내일 아침 개발팀이 확인해야 할 사항을 포함하세요.
"""

    client = get_client()
    try:
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Summary generation failed: {e}")
        return f"QA 검증 완료. PASS: {pass_count}, WARNING: {warn_count}, FAIL: {fail_count}"
