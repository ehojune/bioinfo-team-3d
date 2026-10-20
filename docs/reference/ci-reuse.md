# CI 문서 판별·결과 재사용 (#788)

lookup-only 정책 근거입니다. `.github/workflows/test.yml`과 `scripts/ci_skip.py`가 구현 기준입니다.

| 판정 | 실행 |
|---|---|
| 허용 경로의 prose만 변경하고 보호 내용은 동일, 또는 schema에 맞는 patch entry | README/integrations·vocab·topic checklist·private-path 기본값·encoding·기록·공개 검사, PR patch-note 검사 |
| main checkout과 일치하는 전체 PR 검증 증거 | 위 문서 검사 유지, 중복 test gate 재사용 |
| 그 밖의 변경·증거 불명 | Ubuntu 3.10/3.12 전체 suite, Windows 3.12 전체 suite, clean-install, 공식 RO-Crate validator, 긴 MCP gate |

## 문서 경계

- PR은 실제 synthetic merge의 base부터 checkout까지, main은 push의 before부터 checkout까지 전부 비교합니다. 마지막 notes commit만 보지 않습니다.
- README 한영판·STATUS·생성 patch-note Markdown·docs Markdown과 정해진 schema의 평면 patch entry만 후보입니다. 실행 mode·symlink·submodule·알 수 없는 경로는 full입니다.
- 코드·설정·패키지·dependency·workflow·test 변경은 full입니다. manual·AGENTS/HANDOFF·정책 경로는 문장 경계의 줄바꿈만 후보이며, Markdown 내용·문단·목록·제목·예제·link가 모두 같아야 합니다. 내용 변경·명령 줄바꿈·모호한 markup은 full입니다.
- 기존 dependency인 markdown-it-py로 fenced/indented/inline code, HTML, link·image 목적지와 reference 정의를 보호합니다. 그 내용·순서가 같으면 README처럼 예제가 있는 문서도 prose만 바꿀 수 있습니다. 예제 추가·삭제·수정은 full입니다.
- 바뀐 본문은 문장부호로 끝나는 문장만 허용합니다. CLI 이름 목록과 별개로 실행 지시어·파일/경로·옵션·따옴표가 있는 변경 문장은 모호하므로 full입니다. 이런 표현을 쓴 일반 prose도 보수적으로 full이지만, 그대로인 예제 주변의 일반 문장은 줄입니다. 기존 명령·설정 형태, 짧은 fragment, 닫히지 않은 fence/backtick/HTML, 중첩 block 예제와 지원하지 않는 directive도 full입니다. HTML 내부 prose도 보호됩니다.
- 생성 STATUS·patch-note Markdown도 같은 기준입니다. 기존 식별자가 그대로인 prose 수정은 줄지만, inline 식별자·link를 새로 넣는 생성 변경은 full입니다. schema에 맞는 patch-entry YAML은 별도로 판별합니다.
- 승인·안전·게시·가림·설정·상한·검증 등 정책 표현이 바뀐 문장은 README·일반 reference에서도 full입니다. PR 첫 실행부터 전체 base diff로 판별하며 이전 run은 필요하지 않습니다.
- 축소 compatibility suite는 없습니다. assertions와 safety fixtures는 기존 전체 suite에 그대로 남습니다.

## 재사용 증거

main의 해당 commit에 병합된 같은 저장소 PR의 최신 matching run만 후보입니다. 허용 workflow는 같은 저장소의 `test.yml` ID·path이며 event는 pull_request입니다. fork PR은 전체 gate를 유지하고 재사용 manifest 생성만 생략합니다.

1. GitHub run이 완료·성공이고 7일 이내이며 fork가 아니어야 합니다. 더 최신 matching run이 실패·취소·진행 중이면 이전 성공으로 돌아가지 않습니다.
2. REST run/job의 head SHA는 PR branch head입니다. producer는 이벤트의 PR 번호·저장소 ID·head/base snapshot과 실제 `GITHUB_SHA` checkout을 따로 기록합니다. commit API에서 checkout의 두 parent가 그 base/head이고 tree가 main과 같은지 검증합니다. squash SHA가 달라도 tree는 같아야 합니다.
3. `GITHUB_WORKFLOW_SHA/REF`와 workflow blob도 기록합니다. 지원하는 source는 그 synthetic merge의 같은 저장소·같은 PR merge ref입니다. workflow/helper blob이 source·당시 main base·현재 checkout에서 모두 같아야 합니다. 다른 source commit은 blob이 같아도 full이며, 정책을 바꾼 PR의 첫 main 실행도 full입니다.
4. 최신 attempt의 Ubuntu 두 버전·Windows·clean-install·공식 validator·MCP와 changes·문서·기록·manifest job이 각각 하나씩 전부 성공해야 합니다. 모든 test job은 같은 immutable checkout을 고정하고 tree를 출력합니다.
5. 전체 gate 성공 뒤 producer가 version 2 JSON을 artifact로 올립니다. run/attempt 이름·API SHA-256 digest·만료 여부·완전한 gate/tree receipt를 확인합니다. ZIP은 크기를 제한하고 JSON 하나만 메모리에서 읽으며 추출·실행하지 않습니다. 외부 storage redirect에는 GitHub credential을 보내지 않습니다.

검색은 최근 20 run, API 응답은 2 MiB, artifact는 256 KiB ZIP·32 KiB JSON으로 제한합니다. REST의 PR 연결 정보가 나중 head로 바뀌거나 merge object를 읽지 못해도 full입니다. API 오류·범위 초과·누락·중복·불일치·부분 rerun도 full입니다. 재사용 결과로 새 manifest를 만들지 않습니다. 실제 artifact 왕복·절감 시간·재사용률은 아직 측정하지 않았습니다.

Concurrency는 workflow와 PR 번호로 묶어 PR 후속 head만 이전 실행을 취소합니다. main·dispatch는 run/attempt별 group으로 분리해 대기 중 실행도 서로 취소하지 않습니다. #787의 3.10 25분 제한·faulthandler·stall snapshot은 그대로입니다.

GitHub 계약: [concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency), [PR merge SHA](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request), [workflow SHA](https://docs.github.com/en/actions/reference/workflows-and-actions/variables), [attempt jobs](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt), [artifact digest·download](https://docs.github.com/en/rest/actions/artifacts).
