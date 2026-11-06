## 단계
#612 rev12: 열린 issue 정리 C(#430, #612, #151, #625, #621, #546). 읽기 전용 감사이고 코드는 바꾸지 않았다.

## 한 일
- `docs/reference/issue-hygiene-20261107-c.md`에 판정과 근거를 적었다.
- #430은 처음에 닫았다가 다시 열었다: PR #578이 PI 결정과 첫 번째 안(staging)을 구현했지만, 남은 쓰기 있는 클러스터 확인을 #1295 D단계(읽기 전용)가 추적하지 않기 때문이다.
- #430·#612·#151·#625·#621·#546은 열어 두고 각 issue에 남은 일과 재개 조건을 댓글로 남겼다. #625는 단계별 등급을 "1단계 완료, CSO 선택 미완"으로, 요인 실험을 "mock 전용, live 비용 경계가 승인보다 먼저"로 정정했다.
- 총괄 요청 두 건: #612는 `RequestIn.run_where`, `labhq send --wsl`, 웹 요청 폼, CSO 입력이 main에 있음을 확인했다. #151은 `labhq/vocab/edam_*.yaml`·`scripts/edam_subset.py`가 core로 남아 있음을 확인했다.

## 확인
- `tests/test_wsl_setup_card_612.py` 39건, EDAM·input_fit·shadow_remove 시험 51건이 통과했다(Windows 로컬).

## 하지 않은 것
- 실제 WSL·HPC·Atlas 실행은 하지 않았다. #151의 79개 매핑 개별 검수 기록은 열어 보지 않았다.
