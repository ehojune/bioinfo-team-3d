## 결론

논문 재현 파견직을 CP1 전에 채용·배정합니다. 최신 main을 병합하고 봇 P1 세 건을 수정했습니다. Refs #549.

## 바뀐 것

- 충돌 난 웹 테스트 목록은 양쪽을 보존하고 종료 창·공통 디자인 CSS의 로드 순서를 맞췄습니다.
- 채용 task 복구는 batch·논문별로 구분합니다. 다른 논문의 완료 receipt를 재사용하지 않습니다.
- 새 일반·연구 계획은 `step_ids`가 필수이며 최소 한 단계가 필요합니다. 저장된 연구 계획의 호환성은 유지합니다.
- 재시작하면 현재 batch의 active 파견직 러너를 기다립니다. 기한이 지나면 receipt를 보존하고 요청을 중단합니다.
- 승인·수습·인재풀 재고용·실패 시 정규직 재배정은 기존 요청별 기록을 따릅니다.

## 확인

- 병합 후 수정 전 `7329af9`: P1 회귀 **9 failed** → 수정 후 채용 **32 passed**.
- 관련 **273 passed/1 skipped**, 기존 복구 **100 passed**. Node **48개 파일 통과**.
- Windows 전체 pytest **6,308 passed/66 skipped**(738.52초).
- 공개 검사·패치노트·기록 목차 검사 통과. merge commit을 포함해 패치노트 행을 남겼습니다.

## 남은 지적

- 전체 pytest에 subprocess 출력의 `UnicodeDecodeError` thread warning이 남습니다. 실패한 테스트는 없습니다.
- 실제 Paper2Agent 채용·PI/trial 실행 검증과 PR 병합은 수행하지 않았습니다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead
