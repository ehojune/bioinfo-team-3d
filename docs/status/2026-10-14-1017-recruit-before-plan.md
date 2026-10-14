## 결론

논문 분석 재현에 필요한 파견직을 CP1 전에 채용하고 이번 요청의 해당 단계에 배정합니다. Refs #549 (B·PI 선호).

## 바뀐 것

- 연구·일반 계획에 재현=기술자형/full, 기존·신규 병렬 비교, 공동 Nextflow 순서를 명시합니다.
- 채용을 요청당 한 장으로 승인하고 수습 통과 뒤 바로 투입합니다. 같은 논문 인재풀은 hash·수습 기록 확인 후 재고용합니다.
- 거절·실패·시간 초과는 초안의 정규직에게 재배정합니다. Paper2Agent doctor 실패는 생략 사유를 계획·CP1에 남깁니다.
- 중복 승인·재변환을 막는 요청별 기록, 기존 채용 API와 분리한 실행 집계, 매뉴얼·모의 회귀 테스트를 추가했습니다.

## 확인

- main `d56253a`: 새 회귀 23 failed → branch 23 passed.
- 관련 368 passed, 채용·기존 계약·모의 e2e 106 passed. 수정 후 계약·웹·채용 148 passed, 1 skipped. 표준 Node 46개 파일 통과.
- 공개 검사·diff 검사 통과. Windows 전체 재검증: 6,213 passed, 66 skipped, semantics on/off 기록 비교 1 failed. 해당 두 경우를 단독 재실행해 2 passed를 확인했습니다. Windows 전체 최종 판정은 CI에서도 확인합니다.

## 남은 지적

- semantics on/off 기록 비교가 로컬 전체 실행에서 한 번 달랐고 단독 재실행에서는 재현되지 않았습니다.
- 실제 Paper2Agent 변환·채용과 PI/trial 실행·설정 변경은 수행하지 않았습니다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead
