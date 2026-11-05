# #983 시설팀 복구 상태 계약

기준 `da3eff9f`; [계약·근거](../reference/facilities-recovery-contract-983.md). 요청 대상, 실제 담당, 답변·거부, 승인, fix 실행과 원 단계 결과를 분리했다. CSO fallback과 자동 진단 offline 처리도 다르다. package `applied`는 지시이며 성공 증거가 아니다. 기존 기록을 조합하는 projection과 불명 상태 보존을 후속으로 제안한다.

격리 fake hub에서 진단 답변 후 원 단계 실패 유지, 승인 거절·timeout 시 재개 없음, 승인 뒤 성공·실패 재개를 확인했다. 기존 fallback·doctor·fix·진단 회귀를 함께 실행했다.

- `pytest tests/test_facilities_recovery_contract_983.py tests/test_facilities_fallback_983.py tests/test_doctor_facilities_support_983.py tests/test_facilities_diagnosis.py tests/test_facilities_fixes.py -q`: **151 passed**.
- `pytest tests/test_doctor.py tests/test_ask_review6.py -q`: **86 passed**. 기존 정책 거부 및 roster 회귀 포함.
- 기존 가상환경 사용, fake dispatch와 임시 store만 사용. 실제 시설팀·엔진·로그인·설치·운영·HPC·배포는 실행하지 않았다.

제품 수정은 없다. #983은 OPEN, 실제 설치판 복구는 **UNVERIFIED**다. CI와 동일 head 리뷰 결과는 PR에서 확인한다.
