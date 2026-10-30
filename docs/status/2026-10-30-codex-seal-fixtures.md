## 단계
#1063 · #666 독립 supervisor/seal synthetic acceptance fixture. Draft/hold이며 통합 검토 전에는 병합하지 않습니다.

## 한 일
- 19개 corpus case와 pure validator: 독립 witness, run/nonce·supervisor identity, source/parent identity·link, 연속 관측, 정상 종료 후 descendant/socket 정리를 검사합니다.
- endpoint hash가 같아도 실행 중 변경·원복을 거부합니다. 필수 관측 삭제·identity 변경·잘못된 입력 회귀를 추가했습니다.
- future producer adapter는 명시적으로 unsupported입니다. 실제 보호 판정은 모든 결과에서 UNVERIFIED입니다.
- 담당 파일만 수정했습니다. W1 producer·W3 harness·daemon·adapter·manual·CI inventory는 수정하지 않았습니다.

## 테스트 결과
- `python -m pytest tests/test_process_boundary_seal_contract.py -q`: 123 passed.
- Python 3.10 syntax, public check, patch-note 검사는 PR 근거에 기록합니다.
- 같은 head CI·실제 Codex review는 PR에서 추적합니다. 실행·보호 PASS를 뜻하지 않습니다.

## 막힌 점
- Windows discovery 등록은 별도 scoped claim이 필요합니다. `config/ci/windows-boundary-v1.json`의 sorted `known_tests`에 `tests/test_process_boundary_seal_contract.py`를 추가하고 `boundary_cohorts`에도 같은 exact key를 추가해야 합니다. 등록 전 CI PASS는 주장하지 않습니다.
- [관측 계약·adapter 전제](../reference/process-boundary-seal-fixtures.md). 실제 supervisor 인증·연속 관측·OS 보호·실행 trial은 미검증입니다.
