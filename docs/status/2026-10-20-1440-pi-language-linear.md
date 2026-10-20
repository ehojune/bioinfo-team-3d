## 2026-10-20 14:40 — `pi-language-linear`

## 단계
P1 — 긴 보고서의 경로 판별 지연 제거. Fixes #810.

## 한 일
- 경로 존재 판별을 고정 길이 패턴으로 바꿔 긴 접두문에서 반복 탐색을 없앴습니다. 입력을 자르지 않아 끝의 경로도 찾습니다.
- Unicode·상대/절대 경로·코드 감지와 25,000·100,000자 회귀 검사를 추가했습니다. 기존 통역 test의 Git provenance만 fixture에서 가짜 값으로 바꿨습니다.

## 테스트 결과
- mock-only guard 유지: 관련 146개 통과(85.58초). 제한 자료·예산·timeout·종료 안전 검사를 유지했습니다.
- 지정 followup test: 수정 전 23.54초 → 수정 후 7.10초. **1초 목표는 미달**입니다.
- 단일 test profile: 6.99초 중 SQLite transaction 종료 5.63초, 경로 판별 25회 합계 0.058초. 저장 정책은 변경하지 않았습니다.
- 순수 판별 25,000자: 약 0.99–1.06초 → 0.00065–0.00082초. 경로 패턴은 짧은 문자열 55,987개에서 기존 판정과 일치했습니다.
- 첫 검증의 Git subprocess 시도는 guard가 차단했습니다. 자식은 시작되지 않았고 fixture 수정 뒤 관련 검사가 통과했습니다.

## 확인
- 공개 검사·patch-notes·notes-index 검사 통과. Python 3.10·Linux 실행은 CI 확인 전입니다.
- 연구·PI 실행·HPC·bench·배포는 하지 않았습니다. 최종 CI·리뷰·병합은 총괄이 이어갑니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
