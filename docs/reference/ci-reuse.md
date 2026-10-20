# CI 문서 판별·결과 재사용 (#788)

lookup-only 정책 근거입니다. `.github/workflows/test.yml`과 `scripts/ci_skip.py`가 구현 기준입니다.

| 판정 | 실행 |
|---|---|
| 허용 경로의 prose만 변경하고 보호 내용은 동일, 또는 schema에 맞는 patch entry | README/integrations·vocab·topic checklist·private-path 기본값·encoding·기록·공개 검사, PR patch-note 검사 |
| main checkout·profile까지 일치하는 전체 PR gate 증거 | 위 문서 검사 유지, 중복 test gate 재사용 |
| 그 밖의 변경·증거 불명 | Ubuntu 3.10/3.12 전체 suite, Windows 3.12 경계 profile, clean-install, 공식 RO-Crate validator, 긴 MCP gate |

## 문서 경계

- PR은 실제 synthetic merge의 base부터 checkout까지, main은 push의 before부터 checkout까지 전부 비교합니다. 마지막 notes commit만 보지 않습니다.
- README 한영판·STATUS·생성 patch-note Markdown·docs Markdown과 정해진 schema의 평면 patch entry만 후보입니다. 실행 mode·symlink·submodule·알 수 없는 경로는 full입니다.
- 코드·설정·패키지·dependency·workflow·test 변경은 full입니다. manual·AGENTS/HANDOFF·정책 경로는 내용이 같은 줄바꿈만 후보이며, Markdown 내용·문단·목록·제목·예제·link가 모두 같아야 합니다. 내용 변경·명령 줄바꿈·모호한 markup은 full입니다.
- 기존 dependency인 markdown-it-py로 fenced/indented/inline code, HTML, link·image 목적지와 reference 정의를 보호합니다. 그 내용·순서가 같으면 README처럼 예제가 있는 문서도 prose만 바꿀 수 있습니다. 예제 추가·삭제·수정은 full입니다.
- 바뀐 본문은 문장부호로 끝나는 문장만 허용합니다. CLI 이름 목록과 별개로 실행 지시어·파일/경로·옵션·따옴표가 있는 변경 문장은 모호하므로 full입니다. 이런 표현을 쓴 일반 prose도 보수적으로 full이지만, 그대로인 예제 주변의 일반 문장은 줄입니다. 기존 명령·설정 형태, 짧은 fragment, 닫히지 않은 fence/backtick/HTML, 중첩 block 예제와 지원하지 않는 directive도 full입니다. HTML 내부 prose도 보호됩니다.
- 문단 수정은 작은 positive grammar에 한정합니다. 한국어는 고정된 문서 주어+정도 표현+형용사 하나만 허용하며 임의 prefix·행동/보고 꼬리·행동이 섞인 문장·붙여 쓴 지시문 제목은 full입니다. `설명이 더 명확해졌습니다.`의 형용사 수정은 후보지만 `조립하고 결과를 확인합니다.`의 행동 수정은 full입니다. 영어 copular 설명·문서 명사구는 유지하며 `Build with gradle clean.`·미분류 prose도 full입니다. 같은 Markdown reflow와 별도 설명 문단 수정은 유지하지만 일반 자연어/명령의 완전한 해석을 뜻하지 않습니다.
- 생성 STATUS·patch-note Markdown도 같은 기준입니다. 기존 식별자가 그대로인 prose 수정은 줄지만, inline 식별자·link를 새로 넣는 생성 변경은 full입니다. schema에 맞는 patch-entry YAML은 별도로 판별합니다.
- 승인·안전·게시·가림·설정·상한·검증 등 정책 표현이 바뀐 문장은 README·일반 reference에서도 full입니다. PR 첫 실행부터 전체 base diff로 판별하며 이전 run은 필요하지 않습니다.
- 주제 명사와 별개로 영어·한국어의 요구·허용·금지 표현을 이전/새 본문 양쪽에서 검사합니다. 해당 inline 문단의 문구·문맥·강조·container가 바뀌면 full이며, capability·권고처럼 의미가 모호한 표현도 보호합니다. 같은 문단의 일반 문구를 바꾸는 경우도 full로 남깁니다. 내용과 Markdown 구조가 모두 같은 soft wrapping만 허용하며 별도 일반 prose 문단의 수정은 기존 기준을 따릅니다. 회귀는 `tests/test_ci_normative_prose.py`입니다.
- Linux는 전수 node를 유지합니다. Windows PR/main은 아래 경계 profile이며 full과 동등하지 않습니다. assertions·safety fixtures는 삭제하지 않고 Windows full은 nightly/dispatch로 유지합니다.

## 재사용 증거

main의 해당 commit에 병합된 같은 저장소 PR의 최신 matching run만 후보입니다. 허용 workflow는 같은 저장소의 `test.yml` ID·path이며 event는 pull_request입니다. fork PR은 전체 gate를 유지하고 재사용 manifest 생성만 생략합니다.

1. GitHub run이 완료·성공이고 7일 이내이며 fork가 아니어야 합니다. 더 최신 matching run이 실패·취소·진행 중이면 이전 성공으로 돌아가지 않습니다.
2. REST run/job의 head SHA는 PR branch head입니다. producer는 이벤트의 PR 번호·저장소 ID·head/base snapshot과 실제 `GITHUB_SHA` checkout을 따로 기록합니다. commit API에서 checkout의 두 parent가 그 base/head이고 tree가 main과 같은지 검증합니다. squash SHA가 달라도 tree는 같아야 합니다.
3. `GITHUB_WORKFLOW_SHA/REF`와 workflow blob도 기록합니다. 지원하는 source는 그 synthetic merge의 같은 저장소·같은 PR merge ref입니다. workflow/helper·selector/profile·dev dependency·conftest/guard의 blob이 source·당시 main base·현재 checkout에서 모두 같아야 합니다. 다른 source commit은 blob이 같아도 full이며, 정책을 바꾼 PR의 첫 main 실행도 full입니다.
4. 최신 attempt의 Ubuntu 두 버전·Windows·clean-install·공식 validator·MCP와 changes·문서·기록·manifest job이 각각 하나씩 전부 성공해야 합니다. 모든 test job은 같은 immutable checkout을 고정하고 tree를 출력합니다.
5. 전체 gate 성공 뒤 producer가 version 3 JSON을 artifact로 올립니다. run/attempt 이름·API SHA-256 digest·만료 여부·완전한 gate/tree/profile receipt를 확인합니다. ZIP은 크기를 제한하고 JSON 하나만 메모리에서 읽으며 추출·실행하지 않습니다. 외부 storage redirect에는 GitHub credential을 보내지 않습니다.

검색은 최근 20 run, API 응답은 2 MiB, artifact는 256 KiB ZIP·32 KiB JSON으로 제한합니다. REST의 PR 연결 정보가 나중 head로 바뀌거나 merge object를 읽지 못해도 full입니다. API 오류·범위 초과·누락·중복·불일치·부분 rerun도 full입니다. 재사용 결과로 새 manifest를 만들지 않습니다. 실제 artifact 왕복·절감 시간·재사용률은 아직 측정하지 않았습니다.

## 병렬 실행·Windows 경계 (개발 중)

- CI outer Linux 3.10/3.12 전수는 `pytest-xdist==3.8.0`의 `-n auto --dist loadfile`로 실행합니다. global addopts/PYTEST_ADDOPTS는 없고 nested pytest는 직렬 기본값입니다. auto worker 수는 측정값으로 기록하며 4개로 가정하지 않습니다.
- Windows PR/main은 `windows-boundary-v1`, nightly/dispatch는 `windows-full-v1`입니다. `config/ci/windows-boundary-v1.json`의 검토 이유와 SHA-256이 같은 pure 파일만 제외합니다. OS/runtime/safety 파일·내용, 변경/신규/미분류 파일은 기본 포함합니다. pure review는 runtime Python·schema/catalog·scripts·pyproject와 검토한 fixture helper fingerprint에 묶입니다. 관련 없는 runtime merge라도 전수로 복귀하는 보수적 만료를 유지합니다. 검토된 10파일(문자열/Markdown·claim/schema·offline XML·fake API/계획 비용)은 profile의 digest·이유로 확인합니다. 전체 purity audit나 Windows full 대체를 뜻하지 않습니다.
- Windows-only 회귀가 제외 파일에 생기면 nightly/dispatch에서 늦게 발견될 위험이 남습니다. Linux 전수는 Windows 실행의 증거가 아닙니다. 5분은 목표이며 미측정입니다. 안전 검증을 줄여 시간을 맞추지 않습니다.
- controller가 원래 operator HOME을 workerinput으로 전달하고 worker는 inherited guard 신원과 일치해야 collection을 시작합니다. collection/per-test HOME·state·cache·DB/log는 기존 worker별 basetemp/tmp_path 안입니다. AST가 확인한 port/process capability 호출·free_port 예약과 관련 local test helper는 job별 file lock으로 setup~teardown을 직렬화합니다. Hub/Runner/ASGI mock 이름·import·문자열만으로 lock하지 않습니다. 호출 판별은 보수적이며 외부 fixture의 완전한 정적 증명을 뜻하지 않습니다. production fsync·SQLite FULL·atomic replace·Job 코드는 바꾸지 않습니다.
- 각 CI job artifact에는 full/selected node 목록·제외 이유, CPU·실제 worker 수·platform, setup/pytest/job queue 시간, 실패/skip 수·최장 파일·host-resource lock 대기가 남습니다. worker collection 목록은 서로 같아야 합니다. 3.10 controller 진단과 25분 상한을 유지하고 worker도 5분마다 node/thread/task stack을 별도 기록합니다.
- v3 manifest는 Linux full/loadfile·Windows boundary 등 gate별 profile과 위 모든 policy blob을 결합합니다. Windows full·과거 serial/v2·다른 workflow/profile/tree 결과는 교환하지 않습니다. 변경한 정책의 자기 인증은 허용하지 않으므로 첫 main 검증은 전수 gate 경로입니다. parent/identity mismatch를 재사용 증거로 허용하지 않습니다. 확인된 snapshot drift의 no-reuse 결과는 아래처럼 별도로 처리합니다.

근거: [xdist distribution](https://pytest-xdist.readthedocs.io/en/stable/distribution.html), [worker isolation](https://pytest-xdist.readthedocs.io/en/stable/how-to.html), [3.8.0 Python >=3.9](https://pypi.org/project/pytest-xdist/3.8.0/). 병렬 실행·Windows 성능·artifact 재사용은 실제 CI 확인 전입니다.

run 39461236144에서는 event·run association·현재 PR의 base가 `d0c834a`, 검증 merge의 첫 parent와 main ref가 `2ce8778`인 snapshot drift를 확인했습니다. GitHub 갱신 시점과 이전 run의 원인은 미확정입니다. 같은 repo/run/attempt/workflow·9개 성공 job·동일 tree/순서 있는 merge parents·PR merge SHA/head·main ref·완전한 forward compare를 독립 확인한 경우만 producer가 `reusable=false`로 종료합니다. event base를 바꾸거나 재사용 manifest를 쓰지 않으며 upload도 하지 않습니다. consumer는 artifact가 없으면 full입니다. API 증거가 없거나 다르거나 다른 manifest 오류가 있으면 기존 실패를 유지합니다. 안정된 binding의 manifest 형식과 consumer의 기존 trusted-base policy·digest·identity 검사는 그대로입니다.

Concurrency는 workflow와 PR 번호로 묶어 PR 후속 head만 이전 실행을 취소합니다. main·dispatch는 run/attempt별 group으로 분리해 대기 중 실행도 서로 취소하지 않습니다. #787의 3.10 25분 제한·faulthandler·stall snapshot은 그대로입니다.

runner 경로는 첫 step의 env에서 받아 UTF-8 GITHUB_ENV로 전달합니다. job.env에서는 runner context를 쓸 수 없습니다([context availability](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability)).

GitHub 계약: [concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency), [PR merge SHA](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request), [workflow SHA](https://docs.github.com/en/actions/reference/workflows-and-actions/variables), [attempt jobs](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt), [artifact digest·download](https://docs.github.com/en/rest/actions/artifacts).
