# #1374 보고서 본문·결과표 대조 (1차: 대조와 표시)

## 결론
KRAS 11차(#910)에서 외부 검토가 찾은 두 부류(본문 수치가 인용한 표에 없음, 증가·감소를 반대로 씀)를 모델 없이 찾아 작성자에게 한 번 돌려주고, 남은 불일치는 보고서 머리에 "검증 전 초안"으로 표시한다. 재현 재실행(입력 준비와 산출 hash 비교)은 #1374의 다음 PR이다.

## 바뀐 것
- `labhq/evidence/report_consistency.py`(신규): 표를 링크한 문장의 수치를 그 표 값과 대조한다. 문장 자릿수 반올림과 %·비율 변환을 허용하고, 기준값·CI 수준·연도·10 미만 정수는 제외한다. 증가·감소 표현은 log2FC 부호, HR·OR·RR·fold change의 1 기준, 문장이 부른 표 행의 log2FC와 대조한다. 생존·반응 문장은 HR 방향을 보지 않는다. 표는 단계가 선언한 산출만 그 단계 폴더에서 읽는다(링크 미추적).
- `labhq/orchestrator/cso.py`: 연구 synthesis(claim 교정 뒤)와 일반 synthesis 뒤에 교정 1회. 고친 본문은 불일치가 줄고 claim 검사 문제가 늘지 않을 때만 채택. `_finish`가 최종 결과를 `report_consistency`와 실행 기록 `## 본문·결과표 대조`에 남긴다. 요청을 막지 않는다.
- `labhq/report_contract.py`: 공통 머리 첫 절 `검증 상태`(검증 전 초안 n건 / 불일치 없음, 재현 실행 안 함(HPC)).
- `labhq/evidence/audit.py`: `labhq verify`가 대조를 다시 계산해 경고와 요약 줄로 보인다. exit code는 바꾸지 않는다.
- manual 13·07 한영.

## 실행한 것
- 새 시험 22건(`tests/test_report_check.py` 21, `tests/test_labhq_verify.py` 1), `tests/test_report_contract.py` 절 순서 검사를 공통 순서 전체 비교로.
- report·verify·bundle 관련 358건, cso·research 329건 통과(로컬).

## 미해결
- 재현 재실행과 산출 hash 비교: #1374 다음 PR. 지금은 영수증 실행이 pass가 아니면 "재현 실행 안 함"만 적는다.
- 읽지 못한 문장(표 링크 없음, 형식 다름)은 건너뛴다. 불일치 0건은 정합성의 증명이 아니다.
