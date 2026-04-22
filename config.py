from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List


class Settings(BaseSettings):
    # LLM
    llm_base_url: str = Field(..., env="LLM_BASE_URL")
    llm_api_key: str = Field(..., env="LLM_API_KEY")
    llm_model: str = Field("gpt-4o", env="LLM_MODEL")

    # goodocs
    goodocs_base_url: str = Field(..., env="GOODOCS_BASE_URL")
    goodocs_api_key: str = Field(..., env="GOODOCS_API_KEY")

    # Git (Bitbucket Server / Gitea)
    git_base_url: str = Field(..., env="GIT_BASE_URL")
    git_api_token: str = Field(..., env="GIT_API_TOKEN")
    git_repos_raw: str = Field(..., env="GIT_REPOS")
    itsm_pattern: str = Field(r"(?:ITSM|STRY)-\d+", env="ITSM_PATTERN")

    # Oracle
    oracle_host: str = Field(..., env="ORACLE_HOST")
    oracle_port: int = Field(1521, env="ORACLE_PORT")
    oracle_service: str = Field(..., env="ORACLE_SERVICE")
    oracle_user: str = Field(..., env="ORACLE_USER")
    oracle_password: str = Field(..., env="ORACLE_PASSWORD")

    # Storage
    report_db_path: str = Field("./data/reports.db", env="REPORT_DB_PATH")

    # Web
    web_host: str = Field("0.0.0.0", env="WEB_HOST")
    web_port: int = Field(8080, env="WEB_PORT")

    # Scheduler
    cron_schedule: str = Field("0 22 * * 1-5", env="CRON_SCHEDULE")
    lookback_hours: int = Field(24, env="LOOKBACK_HOURS")

    @property
    def git_repos(self) -> List[str]:
        return [r.strip() for r in self.git_repos_raw.split(",") if r.strip()]

    @property
    def oracle_dsn(self) -> str:
        return f"{self.oracle_host}:{self.oracle_port}/{self.oracle_service}"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
