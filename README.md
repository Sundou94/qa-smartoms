# QA SmartOMS Agent

ITSM 요구사항(goodocs)과 Git 커밋 코드를 자동으로 비교·검증하는 야간 QA 에이전트입니다.

## 구조

```
qa-smartoms/
├── main.py              # 진입점 (웹서버 + 스케줄러)
├── config.py            # 환경변수 기반 설정
├── scheduler.py         # APScheduler Cron 설정
├── agent/
│   ├── qa_agent.py      # QA 파이프라인 코어
│   ├── llm_client.py    # 사내 LLM (OpenAI 호환) 클라이언트
│   ├── goodocs_client.py# goodocs REST API 클라이언트
│   ├── git_client.py    # Bitbucket/Gitea API 클라이언트
│   └── oracle_client.py # Oracle DB 검증 쿼리 실행
├── models/
│   └── report.py        # Pydantic 데이터 모델
├── storage/
│   └── db.py            # SQLite 리포트 저장
└── web/
    ├── app.py           # FastAPI 대시보드
    └── templates/       # Jinja2 HTML 템플릿
```

## 동작 흐름

```
[Cron: 평일 22:00]
  └─ Git API → 최근 커밋 수집
       └─ 커밋 메시지에서 ITSM/STRY 번호 추출
            └─ goodocs API → 요구사항 조회
                 └─ 사내 LLM → 코드 diff vs 요구사항 비교·판정
                      └─ Oracle DB → 자동 생성 검증 쿼리 실행
                           └─ SQLite → 리포트 저장
                                └─ FastAPI 대시보드 → 아침 확인
```

## 설치 및 실행

```bash
# 의존성 설치
pip install -r requirements.txt

# 환경변수 설정
cp .env.example .env
# .env 파일을 사내 환경에 맞게 편집

# 운영 모드 (웹서버 + 스케줄러)
python main.py

# 즉시 실행 (테스트/수동)
python main.py --run-now
```

## 환경변수 (.env)

| 변수 | 설명 |
|------|------|
| `LLM_BASE_URL` | 사내 LLM API 엔드포인트 |
| `LLM_API_KEY` | LLM 인증 키 |
| `LLM_MODEL` | 사용할 모델명 |
| `GOODOCS_BASE_URL` | goodocs REST API URL |
| `GOODOCS_API_KEY` | goodocs 인증 키 |
| `GIT_BASE_URL` | Bitbucket/Gitea 서버 URL |
| `GIT_API_TOKEN` | Git API 토큰 |
| `GIT_REPOS` | 모니터링할 레포 목록 (`PROJ/repo1,PROJ/repo2`) |
| `ITSM_PATTERN` | 커밋 메시지에서 스토리 번호 추출 정규식 |
| `ORACLE_HOST` | Oracle DB 호스트 |
| `ORACLE_SERVICE` | Oracle 서비스명 |
| `ORACLE_USER` | DB 사용자 |
| `ORACLE_PASSWORD` | DB 패스워드 |
| `CRON_SCHEDULE` | 스케줄 Cron 표현식 (기본: `0 22 * * 1-5`) |
| `LOOKBACK_HOURS` | 커밋 조회 범위 (시간, 기본: 24) |

## 웹 대시보드

`http://localhost:8080` 에서 접근 가능합니다.

- `/` — 리포트 목록 + 최신 요약
- `/reports/{report_id}` — 스토리별 상세 결과 (FAIL/WARNING 자동 펼침)
- `/api/reports` — JSON API
