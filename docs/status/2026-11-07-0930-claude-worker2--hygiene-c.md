## 단계
#612 rev12: 열린 issue 정리 C(#430, #612, #151, #625, #621, #546). 읽기 전용 감사이고 코드는 바꾸지 않았다.

## 한 일
- `docs/reference/issue-hygiene-20261107-c.md`에 판정과 근거를 적었다.
- #430을 닫았다: PI 결정(HPC 요청은 처음부터 끝까지 HPC에서)과 이 issue의 첫 번째 안(검증된 앞 단계 산출을 hash 검증 복사본으로 `hpc/<request>/data/…`에 staging)을 PR #578(`cdd0a570`)이 구현했고, 실제 클러스터 확인은 #1295가 갖는다.
- #612·#151·#625·#621·#546은 열어 두고 각 issue에 남은 일과 재개 조건을 댓글로 남겼다. #625는 단계별 등급을 "1단계 완료, CSO 선택 미완"으로, 요인 실험을 "mock 전용, live 비용 경계가 승인보다 먼저"로 정정했다.
- 총괄 요청 두 건: #612는 `RequestIn.run_where`, `labhq send --wsl`, 웹 요청 폼, CSO 입력이 main에 있음을 확인했다. #151은 `labhq/vocab/edam_*.yaml`·`scripts/edam_subset.py`가 core로 남아 있음을 확인했다.

## 확인
- `tests/test_wsl_setup_card_612.py` 39건, EDAM·input_fit·shadow_remove 시험 51건이 통과했다(Windows 로컬).

## 하지 않은 것
- 실제 WSL·HPC·Atlas 실행은 하지 않았다. #151의 79개 매핑 개별 검수 기록은 열어 보지 않았다.
