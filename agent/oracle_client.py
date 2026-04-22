import logging
import oracledb
from config import settings
from models.report import OracleValidationResult, QAVerdict

logger = logging.getLogger(__name__)

_pool: oracledb.AsyncConnectionPool | None = None


async def get_pool() -> oracledb.AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = oracledb.create_pool_async(
            user=settings.oracle_user,
            password=settings.oracle_password,
            dsn=settings.oracle_dsn,
            min=1,
            max=5,
            increment=1,
        )
    return _pool


async def run_validation_query(
    query: str,
    description: str,
    expected_empty: bool = True,
) -> OracleValidationResult:
    """
    Oracle에서 검증 쿼리를 실행하고 결과를 반환한다.
    expected_empty=True: 결과가 0건이면 PASS (이상 데이터 없음)
    expected_empty=False: 결과가 1건 이상이면 PASS (데이터 존재 확인)
    """
    if not query.strip():
        return OracleValidationResult(
            query=query,
            description=description,
            result_count=0,
            verdict=QAVerdict.SKIP,
            detail="쿼리가 생성되지 않아 건너뜀",
        )

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(query)
                rows = await cursor.fetchall()
                count = len(rows)

        if expected_empty:
            verdict = QAVerdict.PASS if count == 0 else QAVerdict.FAIL
            detail = f"이상 데이터 {count}건 발견" if count > 0 else "이상 데이터 없음"
        else:
            verdict = QAVerdict.PASS if count > 0 else QAVerdict.FAIL
            detail = f"데이터 {count}건 확인" if count > 0 else "예상 데이터 없음"

        return OracleValidationResult(
            query=query,
            description=description,
            result_count=count,
            verdict=verdict,
            detail=detail,
        )
    except oracledb.DatabaseError as e:
        logger.error(f"Oracle query execution error: {e}\nQuery: {query}")
        return OracleValidationResult(
            query=query,
            description=description,
            result_count=0,
            verdict=QAVerdict.WARNING,
            detail=f"Oracle 쿼리 실행 오류: {e}",
        )
    except Exception as e:
        logger.error(f"Unexpected Oracle error: {e}")
        return OracleValidationResult(
            query=query,
            description=description,
            result_count=0,
            verdict=QAVerdict.WARNING,
            detail=f"예상치 못한 오류: {e}",
        )


async def get_schema_hints(table_names: list[str]) -> str:
    """지정된 테이블들의 컬럼 정보를 조회하여 LLM 컨텍스트로 제공한다."""
    if not table_names:
        return ""

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                placeholders = ", ".join(f"'{t.upper()}'" for t in table_names)
                query = f"""
                    SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, NULLABLE
                    FROM ALL_TAB_COLUMNS
                    WHERE TABLE_NAME IN ({placeholders})
                    ORDER BY TABLE_NAME, COLUMN_ID
                """
                await cursor.execute(query)
                rows = await cursor.fetchall()

        if not rows:
            return ""

        lines = ["테이블 스키마:"]
        current_table = None
        for table, col, dtype, nullable in rows:
            if table != current_table:
                lines.append(f"\n[{table}]")
                current_table = table
            lines.append(f"  - {col} {dtype} {'NULL' if nullable == 'Y' else 'NOT NULL'}")

        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"Failed to fetch schema hints: {e}")
        return ""


async def close():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
