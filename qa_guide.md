# QA 검증 가이드

> 이 파일은 LLM이 코드 리뷰 시 참고하는 컨벤션·안티패턴 가이드입니다.
> 웹 대시보드 `/qa-guide` 메뉴에서 직접 수정할 수 있습니다.

---

## 1. 코드 컨벤션

### 네이밍 규칙
- 클래스명: `PascalCase`
- 메서드·변수명: `camelCase` (Java/JS) 또는 `snake_case` (Python)
- 상수: `UPPER_SNAKE_CASE`
- DB 컬럼·테이블명: `UPPER_SNAKE_CASE`

### 패키지·모듈 구조
- Controller → Service → Repository 레이어 분리 준수
- 비즈니스 로직은 Service 레이어에만 위치
- DB 직접 접근은 Repository 레이어에서만 허용

---

## 2. 보안 안티패턴

### SQL Injection
- PreparedStatement 또는 ORM 사용 필수
- 문자열 직접 연결(+, concat, format)로 SQL 구성 금지
  ```java
  // 금지
  String sql = "SELECT * FROM USER WHERE ID = '" + userId + "'";
  // 허용
  String sql = "SELECT * FROM USER WHERE ID = ?";
  ```

### 인증·인가
- API 엔드포인트에 인증 어노테이션 누락 금지 (`@Secured`, `@PreAuthorize` 등)
- 패스워드 평문 저장 금지 — 반드시 해싱(BCrypt 등) 사용
- 민감 정보(토큰, 키)를 로그에 출력 금지

### 입력값 검증
- 외부 입력값은 반드시 유효성 검사 수행
- Null 체크 누락 금지

---

## 3. 성능 안티패턴

### N+1 쿼리
- 루프 내 DB 호출 금지 — 배치 조회로 대체
  ```java
  // 금지
  for (Long id : idList) { repo.findById(id); }
  // 허용
  repo.findAllById(idList);
  ```

### 트랜잭션
- 장시간 작업(외부 API 호출, 파일 I/O)을 트랜잭션 범위 내에 포함 금지
- `@Transactional` 범위를 최소화

---

## 4. 예외 처리

- `Exception`을 통째로 catch하고 무시(empty catch) 금지
- 비즈니스 예외는 커스텀 예외 클래스 사용
- 모든 예외는 적절한 로깅 필수 (`logger.error(...)`)

---

## 5. 공통 QA 체크리스트

- [ ] 요구사항의 모든 인수 조건이 코드에 반영되었는가?
- [ ] 정상 케이스뿐 아니라 예외·엣지 케이스 처리가 있는가?
- [ ] 신규 API는 인증/인가 처리가 적용되었는가?
- [ ] 변경된 로직에 관련 주석 또는 문서가 업데이트되었는가?
- [ ] 하드코딩된 값이 없는가? (URL, 비밀번호, 포트 등)
