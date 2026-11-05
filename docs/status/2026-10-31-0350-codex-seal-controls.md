## 단계
#1063 rev7 · 독립 synthetic seal contract v3. [PR #1064](https://github.com/ehojune/bioinfo-team-3d/pull/1064)는 draft/hold이며 병합하지 않습니다.

## 한 일
- 정상 read/output control과 reason-code 거부를 같은 fixture로 검증합니다. 기존 ancestry 검사는 유지합니다.
- observer identity·sequence/time 출처, 외부 append-only ledger, retained writable handle, broker token·실패, supervisor deadline, descendant/resource 잔류를 검사합니다.
- ledger를 다시 기록한 음성 대조도 거부합니다. expected label은 validator 입력에 쓰지 않습니다.

## 테스트 결과
- `python -m pytest tests/test_process_boundary_seal_contract.py -q`: 598 passed. 기존 29 case·새 53 control, data-only. 실제 process·engine 호출 없음.
- public·diff·patch-note 검사와 최종 head CI·실제 리뷰 결과는 PR에 기록합니다. #1123 문서 gate 수정은 아직 미병합입니다.

## 막힌 점
- 실제 보호 UNVERIFIED. Hash chain은 독립 입력과의 대조이며 실제 서명·storage 접근 제어의 증거가 아닙니다.
- `adapt_producer`는 unsupported. 후속 최소 범위는 외부 observer 인증/ledger를 받는 adapter와 supervisor의 request deadline·reason-coded stop/cleanup evidence 연결입니다. OS seal·handle 취소·독립 inventory는 별도 구현·실행 승인이 필요합니다.
- 공통 문서·제품 코드·CI·provisioning·연구/HPC는 수정하거나 실행하지 않았습니다.
