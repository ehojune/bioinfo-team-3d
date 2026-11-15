# #1413 — Windows Codex 작업 폴더 읽기 거부

## 결과
- 선행 산출 단계의 다른 작업·요청 폴더는 정책 목록에 남기고 native 읽기 거부 목록에서는 제외한다. 설정에서 해석된 개인 경로는 native 거부 대상으로 유지한다.
- 작업 폴더 사이의 OS 수준 읽기 차단은 보장하지 않는다. 남아 있는 기존 ACL은 이 변경으로 지우지 않는다.

## 확인
- `python -m pytest tests/test_access_deny_1413.py -q`: 23 passed, 1 warning. 합성 fixture 검사다.
- 실제 직원 실행, 보호 경로 차단과 시험 인스턴스 배포는 운영 검증 전이며 PR은 그때까지 hold다 (#1427).
