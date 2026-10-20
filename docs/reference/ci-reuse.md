# CI 문서 판별·결과 재사용 (#788)

lookup-only 정책 근거입니다. `.github/workflows/test.yml`과 `scripts/ci_skip.py`가 구현 기준입니다.

| 판정 | 실행 |
|---|---|
| 허용 경로의 예제 없는 prose·schema에 맞는 patch entry만 변경 | README/integrations·기록·공개 검사, PR patch-note 검사 |
| main checkout과 일치하는 전체 PR 검증 증거 | 위 문서 검사 유지, 중복 test gate 재사용 |
| 그 밖의 변경·증거 불명 | Ubuntu 3.10/3.12 전체 suite, Windows 3.12 전체 suite, clean-install, 공식 RO-Crate validator, 긴 MCP gate |

## 문서 경계

- PR은 실제 synthetic merge의 base부터 checkout까지, main은 push의 before부터 checkout까지 전부 비교합니다. 마지막 notes commit만 보지 않습니다.
- README 한영판·STATUS·생성 patch-note Markdown·docs Markdown과 정해진 schema의 평면 patch entry만 후보입니다. 실행 mode·symlink·submodule·알 수 없는 경로는 full입니다.
- 코드·설정·패키지·dependency·workflow·test·AGENTS/HANDOFF와 정책·안전·승인·계약·example 경로는 full입니다.
- 문서 양쪽 중 하나라도 code fence·inline code·들여쓴 예제·HTML·명령 형태가 있으면 full입니다. 예제가 그대로인 문서도 보수적으로 제외합니다.
- 본문은 문장부호로 끝나는 문장만 허용합니다. 명령·설정일 수 있는 짧은 fragment와 복잡한 Markdown도 full입니다.
- 축소 compatibility suite는 없습니다. assertions와 safety fixtures는 기존 전체 suite에 그대로 남습니다.

## 재사용 증거

main의 해당 commit에 병합된 같은 저장소 PR의 최신 matching run만 후보입니다. 허용 workflow는 같은 저장소의 `test.yml` ID·path이며 event는 pull_request입니다.

1. GitHub run이 완료·성공이고 7일 이내이며 fork가 아니어야 합니다. 더 최신 matching run이 실패·취소·진행 중이면 이전 성공으로 돌아가지 않습니다.
2. GitHub commit API의 run SHA·두 parent(base/head)·tree와 CI manifest의 checkout SHA·workflow SHA·tree가 맞아야 합니다. PR branch head만으로는 증명하지 않습니다. squash SHA가 달라도 tree는 같아야 합니다.
3. workflow/helper blob은 현재 checkout뿐 아니라 그 run의 당시 main base와도 같아야 합니다. 정책을 바꾼 PR의 첫 main 실행은 full입니다.
4. 최신 attempt의 Ubuntu 두 버전·Windows·clean-install·공식 validator·MCP와 changes·문서·기록·manifest job이 각각 하나씩 전부 성공해야 합니다. 모든 test job은 같은 immutable checkout을 고정하고 tree를 출력합니다.
5. 전체 gate 성공 뒤 producer가 version 1 JSON을 artifact로 올립니다. run/attempt 이름·API SHA-256 digest·만료 여부·완전한 gate/tree receipt를 확인합니다. ZIP은 크기를 제한하고 JSON 하나만 메모리에서 읽으며 추출·실행하지 않습니다. 외부 storage redirect에는 GitHub credential을 보내지 않습니다.

검색은 최근 20 run, API 응답은 2 MiB, artifact는 256 KiB ZIP·32 KiB JSON으로 제한합니다. API 오류·범위 초과·누락·중복·불일치·부분 rerun은 full로 돌아갑니다. 재사용 결과로 새 manifest를 만들지 않습니다. 실제 절감 시간·재사용률은 아직 측정하지 않았습니다.

GitHub 계약: [PR merge SHA](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request), [workflow SHA](https://docs.github.com/en/actions/reference/workflows-and-actions/variables), [attempt jobs](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt), [artifact digest·download](https://docs.github.com/en/rest/actions/artifacts).
