import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import httpx
from config import settings
from models.report import CommitInfo

logger = logging.getLogger(__name__)

_http_client: httpx.AsyncClient | None = None

# Bitbucket Server API: /rest/api/1.0/projects/{project}/repos/{repo}/commits
# Gitea API:            /api/v1/repos/{owner}/{repo}/commits
# 사내 서버 종류에 따라 GIT_SERVER_TYPE 환경변수로 구분 (기본: bitbucket)


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=settings.git_base_url,
            headers={
                "Authorization": f"Bearer {settings.git_api_token}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
    return _http_client


def _parse_repo_path(repo_entry: str) -> tuple[str, str]:
    """'PROJECT/repo-name' -> ('PROJECT', 'repo-name')"""
    parts = repo_entry.split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid repo format: {repo_entry}. Expected PROJECT/repo-name")
    return parts[0], parts[1]


async def _fetch_commits_bitbucket(
    project: str, repo: str, since: datetime
) -> list[dict]:
    """Bitbucket Server REST API로 커밋 목록을 조회한다."""
    client = get_http_client()
    commits = []
    start = 0
    limit = 50
    since_ts = int(since.timestamp() * 1000)  # milliseconds

    while True:
        url = f"/rest/api/1.0/projects/{project}/repos/{repo}/commits"
        resp = await client.get(url, params={"limit": limit, "start": start})
        resp.raise_for_status()
        data = resp.json()

        for c in data.get("values", []):
            commit_ts = c.get("authorTimestamp", 0)
            if commit_ts < since_ts:
                return commits  # 오래된 커밋은 중단
            commits.append(c)

        if data.get("isLastPage", True):
            break
        start += limit

    return commits


async def _fetch_diff_bitbucket(
    project: str, repo: str, commit_hash: str
) -> str:
    """Bitbucket Server에서 커밋 diff를 조회한다."""
    client = get_http_client()
    url = f"/rest/api/1.0/projects/{project}/repos/{repo}/commits/{commit_hash}/diff"
    try:
        resp = await client.get(url, params={"contextLines": 3})
        resp.raise_for_status()
        data = resp.json()
        # diff 텍스트 재구성
        lines = []
        for diff in data.get("diffs", []):
            src = diff.get("source", {})
            dst = diff.get("destination", {})
            path = dst.get("toString", src.get("toString", "unknown"))
            lines.append(f"--- a/{path}")
            lines.append(f"+++ b/{path}")
            for hunk in diff.get("hunks", []):
                for seg in hunk.get("segments", []):
                    prefix = {"ADDED": "+", "REMOVED": "-", "CONTEXT": " "}.get(
                        seg.get("type", "CONTEXT"), " "
                    )
                    for line in seg.get("lines", []):
                        lines.append(f"{prefix}{line.get('line', '')}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"Failed to fetch diff for {commit_hash}: {e}")
        return ""


async def _fetch_commits_gitea(
    owner: str, repo: str, since: datetime
) -> list[dict]:
    """Gitea API로 커밋 목록을 조회한다."""
    client = get_http_client()
    commits = []
    page = 1
    limit = 50
    since_str = since.isoformat()

    while True:
        url = f"/api/v1/repos/{owner}/{repo}/commits"
        resp = await client.get(
            url, params={"limit": limit, "page": page, "since": since_str}
        )
        resp.raise_for_status()
        data = resp.json()

        if not data:
            break

        commits.extend(data)
        if len(data) < limit:
            break
        page += 1

    return commits


async def fetch_recent_commits(
    repo_entry: str, since: datetime
) -> list[CommitInfo]:
    """
    지정된 레포지토리에서 since 이후 커밋을 조회한다.
    Bitbucket Server와 Gitea 모두 지원한다.
    """
    project, repo = _parse_repo_path(repo_entry)
    raw_commits: list[dict] = []

    try:
        # Bitbucket Server 방식 우선 시도
        raw_commits = await _fetch_commits_bitbucket(project, repo, since)
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (404, 401):
            logger.info(f"Bitbucket path failed ({e.response.status_code}), trying Gitea API")
            try:
                raw_commits = await _fetch_commits_gitea(project, repo, since)
            except Exception as e2:
                logger.error(f"Gitea API also failed for {repo_entry}: {e2}")
                return []
        else:
            logger.error(f"Git API error for {repo_entry}: {e}")
            return []
    except Exception as e:
        logger.error(f"Unexpected error fetching commits for {repo_entry}: {e}")
        return []

    result: list[CommitInfo] = []
    for c in raw_commits:
        # Bitbucket vs Gitea 응답 구조 통합 파싱
        commit_hash = c.get("id", c.get("sha", ""))
        author = (
            c.get("author", {}).get("name", "")
            or c.get("commit", {}).get("author", {}).get("name", "")
        )
        message = (
            c.get("message", "")
            or c.get("commit", {}).get("message", "")
        )
        ts_raw = (
            c.get("authorTimestamp")
            or c.get("created", "")
            or c.get("commit", {}).get("author", {}).get("date", "")
        )

        if isinstance(ts_raw, (int, float)):
            ts = datetime.fromtimestamp(ts_raw / 1000, tz=timezone.utc)
        elif isinstance(ts_raw, str) and ts_raw:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        else:
            ts = datetime.now(tz=timezone.utc)

        # diff 요약 (처음 줄만)
        diff_text = await _fetch_diff_bitbucket(project, repo, commit_hash)

        result.append(
            CommitInfo(
                commit_hash=commit_hash[:12],
                author=author,
                message=message,
                timestamp=ts,
                repo=repo_entry,
                diff_summary=diff_text,
            )
        )

    return result


def extract_itsm_ids(commit_message: str) -> list[str]:
    """커밋 메시지에서 ITSM/STRY 스토리 번호를 추출한다."""
    pattern = settings.itsm_pattern
    matches = re.findall(pattern, commit_message, re.IGNORECASE)
    return list(dict.fromkeys(m.upper() for m in matches))  # 중복 제거, 대문자 통일


async def close():
    global _http_client
    if _http_client:
        await _http_client.aclose()
        _http_client = None
