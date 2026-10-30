## 단계
#1063 · #666 독립 supervisor/seal synthetic acceptance fixture. Draft/hold이며 통합 검토 전에는 병합하지 않습니다.

## 한 일
- 20개 corpus case와 pure validator: 독립 witness, run/nonce·supervisor/child identity, source/parent identity·link, 연속 관측, 정상 종료 후 descendant/socket 정리를 검사합니다.
- endpoint hash가 같아도 실행 중 변경·원복을 거부합니다. 필수 관측 삭제·identity 변경·잘못된 입력 회귀를 추가했습니다.
- future producer adapter는 명시적으로 unsupported입니다. 실제 보호 판정은 모든 결과에서 UNVERIFIED입니다.
- rev2에서 Windows inventory에 전체 seal contract test를 등록하고 status 파일명에 HHMM을 추가했습니다.

## 테스트 결과
- `python -m pytest tests/test_process_boundary_seal_contract.py -q`: 153 passed.
- Python 3.10 syntax, public check, patch-note 검사는 PR 근거에 기록합니다.
- 같은 head CI·실제 Codex review는 PR에서 추적합니다. 실행·보호 PASS를 뜻하지 않습니다.

## 막힌 점
- 최종 head CI·실제 review 확인 뒤에도 #666 통합 판단 전에는 draft/hold를 유지합니다.
- [관측 계약·adapter 전제](../reference/process-boundary-seal-fixtures.md). 실제 supervisor 인증·연속 관측·OS 보호·실행 trial은 미검증입니다.
