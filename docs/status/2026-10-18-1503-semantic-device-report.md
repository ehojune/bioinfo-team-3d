## 결론

PR #743에서 요청별 온톨로지·의미 장치 보고서를 자동 생성합니다. Fixes #737.

## 바뀐 것

- 요청 묶음의 `semantic_report.md`·`semantic_report.json`에 장치 6개의 상태·입력·판단·영향·코드·기록 위치를 남깁니다.
- 체크리스트 항목별 계획 포함·담당 단계·reviewer 판정과 pack 적용·제외, 그림자 후보·참조를 기록합니다.
- CP2 카드와 실행 기록이 보고서를 가리키며 정리본은 `run/semantic_report.md`에 게시합니다.
- 기본은 켜짐이며 `research.semantic_report: false`로 끌 수 있습니다.

## 확인

- 새 회귀는 main 상당 상태에서 import 실패, 구현 뒤 5 passed입니다.
- 관련 pytest 659 passed·2 skipped, 설정·초기화 pytest 133 passed입니다.
- 첫 CI의 Python 3.10에서 7 failed를 확인했습니다. shadow 제거·CP4 저장 경로 수정 뒤 실패 7건과 보고서 회귀 5건이 통과했습니다.
- 둘째 CI는 새 `run/semantic_report.md`를 빠뜨린 기존 파일 목록 기대값 1건만 실패했고, 반영 뒤 해당 test가 통과했습니다.
- `py_compile`, `git diff --check`, 공개 금지 항목 수동 검사는 통과했습니다.
- `scripts/check_public.sh`는 Windows Git Bash에서 30초 동안 출력 없이 멈춰 중단했습니다.
- `docs/manual.md`: 159,391자 → 159,649자(+258자).

## 남은 것

- 수정 head의 CI 결과 확인이 남았습니다. 리뷰 지적 대응·리뷰 요청·병합은 총괄이 이어갑니다.
