## 결론

선행 연구에서 고른 논문을 CP1 전에 자문형 스킬로 준비하고 지정한 정규직의 해당 단계에만 장착합니다. Refs #549 (C).

## 바뀐 것

- `paper_skills`: DOI/PMID·방법 적합성·담당 단계, 최대 2편. 코드·데이터가 있는 논문 우선.
- recruiter 변환·인재풀 hash 재사용. 계약·수습 없이 실행하며 변환 실패·미설치는 경고 후 계속.
- Claude 임시 plugin·Codex 작업 폴더 스킬. 매 turn 뒤 제거하고 논문 ID·Paper2Agent commit·hash·장착 단계를 부록과 묶음에 기록.
- 스킬이 선택된 일반 요청은 계획의 담당 단계대로 실행합니다. 단독 경로가 담당 직원을 바꾸는 일을 막기 위한 처리입니다.

## 확인

- 기준 main `0cf446a`: 새 회귀 14 failed → branch 14 passed.
- 관련 pytest 275 passed, 2 skipped. Node 43개 파일 통과.
- 공개 검사·diff 검사 통과. 계약 고정값·runner 호출명 갱신 후 관련 182 passed, 1 skipped.
- Windows 전체 첫 실행: 6,100 passed, 66 skipped, 53 failed. 계약 고정값/호출명과 Node shim 환경 원인을 처리했고 전체 재검증은 순차 대기 중입니다.

## 남은 지적

- 스킬은 변환한 러너의 로컬 인재풀을 사용합니다. 담당 직원이 다른 러너에 있으면 장착 경고를 남기며 원격 전송은 구현하지 않았습니다.
- 실제 Paper2Agent 변환·PI/trial 실행·설정 변경·리뷰 요청·병합은 수행하지 않았습니다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead
