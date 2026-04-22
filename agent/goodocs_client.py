import logging
import httpx
from config import settings
from models.report import ITSMStory

logger = logging.getLogger(__name__)

_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=settings.goodocs_base_url,
            headers={
                "Authorization": f"Bearer {settings.goodocs_api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
    return _http_client


async def fetch_story(story_id: str) -> ITSMStory | None:
    """
    goodocs REST API에서 ITSM 스토리 요구사항을 조회한다.
    API 경로: GET /stories/{story_id}
    실제 사내 goodocs API 스펙에 맞게 경로와 필드 매핑을 조정하세요.
    """
    client = get_http_client()
    try:
        resp = await client.get(f"/stories/{story_id}")
        resp.raise_for_status()
        data = resp.json()

        # goodocs 응답 구조에 맞게 필드를 매핑합니다.
        # 실제 API 응답 키 이름이 다르면 아래를 수정하세요.
        return ITSMStory(
            story_id=story_id,
            title=data.get("title", data.get("summary", "")),
            description=data.get("description", data.get("body", "")),
            acceptance_criteria=data.get(
                "acceptance_criteria",
                data.get("acceptanceCriteria", ""),
            ),
            raw=data,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.warning(f"Story {story_id} not found in goodocs")
        else:
            logger.error(f"goodocs API error for {story_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to fetch story {story_id}: {e}")
        return None


async def fetch_stories(story_ids: list[str]) -> dict[str, ITSMStory]:
    """여러 ITSM 스토리를 병렬로 조회한다."""
    import asyncio

    tasks = {sid: fetch_story(sid) for sid in story_ids}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    out: dict[str, ITSMStory] = {}
    for sid, result in zip(tasks.keys(), results):
        if isinstance(result, ITSMStory):
            out[sid] = result
        elif isinstance(result, Exception):
            logger.error(f"Error fetching {sid}: {result}")
    return out


async def close():
    global _http_client
    if _http_client:
        await _http_client.aclose()
        _http_client = None
