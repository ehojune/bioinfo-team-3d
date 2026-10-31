# CI 문서 판별·결과 재사용 (#788)

lookup-only 정책 근거입니다. `.github/workflows/test.yml`과 `scripts/ci_skip.py`가 구현 기준입니다.

| 판정 | 실행 |
|---|---|
| 허용 경로의 prose만 변경하고 보호 내용은 동일, 또는 schema에 맞는 patch entry | README/integrations·vocab·topic checklist·private-path 기본값·encoding·기록·공개 검사, PR patch-note 검사 |
| main checkout·coverage profile까지 일치하는 필수 PR gate 증거 | 위 문서 검사 유지, 중복 test gate 재사용 |
| 그 밖의 변경·재사용 증거 불명 | Ubuntu 3.10/3.12 전수, Windows 3.12 필수 경계, clean-install·공식 validator·긴 MCP gate. Windows 선택 증거 오류는 빠른 실패 |

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
2. run의 PR association이 정확한 빈 목록일 때만 같은 head를 검색 후보로 삼고 `/pulls/N`을 추가 조회합니다. 해당 PR의 merged 상태·main merge SHA·시각·번호·repo ID/name·base/head가 commit 연결 응답과 같아야 하며 아래 증거도 전부 필요합니다. branch/head만으로 허용하지 않습니다. association 필드 누락·malformed·nonempty 불일치는 full입니다.
3. REST run/job의 head SHA는 PR branch head입니다. producer는 이벤트의 PR 번호·저장소 ID·head/base snapshot과 실제 `GITHUB_SHA` checkout을 따로 기록합니다. commit API에서 checkout의 두 parent가 그 base/head이고 tree가 main과 같은지 검증합니다. squash SHA가 달라도 tree는 같아야 합니다.
4. `GITHUB_WORKFLOW_SHA/REF`와 workflow blob도 기록합니다. 지원하는 source는 그 synthetic merge의 같은 저장소·같은 PR merge ref입니다. workflow/helper·selector/profile·dev dependency·conftest/guard의 blob이 source·당시 main base·현재 checkout에서 모두 같아야 합니다. 다른 source commit은 blob이 같아도 full이며, 정책을 바꾼 PR의 첫 main 실행도 full입니다.
5. 최신 attempt의 Ubuntu 두 버전·Windows·clean-install·공식 validator·MCP와 changes·문서·기록·manifest job이 각각 하나씩 전부 성공해야 합니다. 모든 test job은 같은 immutable checkout을 고정하고 tree를 출력합니다.
6. 전체 gate 성공 뒤 producer가 version 7 JSON을 artifact로 올립니다. run/attempt 이름·API SHA-256 digest·만료 여부·완전한 gate/tree/profile receipt를 확인합니다. ZIP은 크기를 제한하고 JSON 하나만 메모리에서 읽으며 추출·실행하지 않습니다. 외부 storage redirect에는 GitHub credential을 보내지 않습니다.

검색은 최근 20 run, API 응답은 2 MiB, artifact는 256 KiB ZIP·32 KiB JSON으로 제한합니다. REST의 PR 연결 정보가 나중 head로 바뀌거나 merge object를 읽지 못해도 full입니다. 빈 association의 예외도 authoritative PR·artifact 증거가 없으면 full입니다. API 오류·범위 초과·누락·중복·불일치·부분 rerun도 full입니다. 재사용 결과로 새 manifest를 만들지 않습니다. 실제 artifact 왕복·절감 시간·재사용률은 아직 측정하지 않았습니다.

## 병렬 실행·Windows 경계

#823의 CI 개선은 PR825로 병합됐습니다. 해당 head의 Windows 실행은 8분14초였으며 [측정 범위와 한계](ci-windows-cohort-823.md)를 봅니다. 다른 head의 실행 시간은 별도로 확인합니다.

- CI outer Linux 3.10/3.12 전수는 `pytest-xdist==3.8.0`의 `-n auto --dist loadfile`로 실행합니다. global addopts/PYTEST_ADDOPTS는 없고 nested pytest는 직렬 기본값입니다. auto worker 수는 측정값으로 기록하며 4개로 가정하지 않습니다.
- 시험마다 300초 상한(`pytest-timeout==2.4.0`, `pyproject.toml`의 `timeout`)이 있고 job 상한은 그대로입니다(#1198). Linux 3.10/3.12는 `timeout_method=signal`로 멈춘 시험 하나만 실패 처리하고 나머지는 이어갑니다. Windows는 SIGALRM이 없어 pytest-timeout의 기본값인 `thread`(phase 수집 필터가 `-o`를 거부해 명령에는 적지 않습니다)로 worker를 덤프 후 종료하며 xdist가 그 시험만 crash로 보고합니다. thread 덤프에는 시험 이름이 없어 `scripts/ci_suite.py`가 상한 5초 전에 시험 이름과 모든 thread 스택을 stderr에 먼저 찍습니다. 느린 시험은 `@pytest.mark.timeout(n)`으로 개별 연장하고, host-resource lock 대기는 상한에 들지 않습니다. 예고는 pytest-timeout이 실제로 쓰는 순서(가까운 marker, `--timeout`, `PYTEST_TIMEOUT` 환경 변수, ini)로 정한 그 시험의 상한을 따릅니다. Windows critical 단계(상한 3분)는 명령에 `-o`를 적을 수 없어 환경 변수 `PYTEST_TIMEOUT=150`으로 시험 상한을 단계보다 짧게 둡니다(#1198).
- 현재 정책(PR825): Windows PR/main은 `windows-boundary-v6`의 명시 필수 file/node 계약과 변경된 Windows 관련 test·직접 OS helper 소비자를 실행합니다. 일반 schema/prompt/논리·미변경 선택 밖 test는 Linux 전수와 Windows nightly/dispatch 전수에 남깁니다. product import의 추정 transitive 추적이나 정책 변경에 따른 전수 확대는 하지 않습니다.
- web shutdown·WMI escape/ACK의 알려진 실패를 포함한 5개 callable은 mandatory입니다. collection/선택에서 하나라도 없으면 실패합니다. Job·identity·cleanup, drive/PowerShell/private sandbox, SQLite/durable/lock, doctor/CLI/MCP/receipt/hook은 명시 cohort로 유지합니다. 이 범위는 full Windows와 동등하지 않으며 제외된 Windows 조합의 발견이 nightly로 늦어질 위험이 있습니다.
- routine Windows job은 setup·collection을 포함해 18분입니다(목표는 10분이며 `summary.json`의 `over_ten_minutes`로 계속 잽니다). 첫 serial phase는 3분, 나머지 4-worker phase는 13분(성공한 pool 단계 최악값 499초의 1.5배, #1076)이며 두 phase의 실제 선택 union을 한 번씩 실행합니다. 두 브라우저가 각 60초를 쓸 때도 native 검사·collection 시간을 남기기 위한 재배분입니다. timeout은 실패입니다. pip 2분, report/upload 각 1분의 step bound와 always artifact 업로드를 두며 hard job 취소 때 업로드까지 보장하지는 못합니다. nightly/manual은 full Windows(종전 360분 job 상한 유지)입니다. 병합된 #825의 실제 완료 시간은 [측정 기록](ci-windows-cohort-823.md#실제-ci-측정)에 있습니다. 다른 head의 통과를 대신하지 않습니다.
- critical은 두 native 파일 전체(현재25개, 추가 case도 포함)와 resizable browser case 1개만 수집합니다. 명시한 whole-file/node 인수·기본 discovery와 필터 부재를 확인하고, worker 없이 각 setup/call/teardown PASS·no-SKIP과 5개 mandatory callable을 검증한 뒤 pool을 엽니다. browser 미설치·내부 TAP의 3 PASS/0 SKIP 미달도 실패합니다. critical 앞의 `Warm Chrome` step(`scripts/ci_chrome_warmup.py`)이 시험과 같은 플래그·빈 프로필로 Chrome을 한 번 띄워 DevTools 포트와 첫 페이지 target까지의 시간을 출력합니다. **첫 Chrome의 cold start 검증은 이 step이 맡고** 상한은 드라이버 기본 `START_MS`와 같은 60초이며, 넘으면 이 step이 `Chrome warm-up failed`로 실패합니다(첫 Chrome만 12~51초로 길어지는 실측, #1107). 이어지는 resizable의 각 viewport는 Node 시작·소유 자원 정리를 포함한 합산 60초 상한 안에서 **데워진 Chrome의** 시작 최대 30초, UI 검사 최대 20초를 받습니다. 시험의 30초 상한은 바꾸지 않았고 재시도·skip 없이 기존 UI 단언을 유지하며 nightly/manual도 같은 격리 뒤 나머지 전수를 실행합니다.
- 실행 identity `windows-critical-serial-v2`는 critical의 좁은 실제 수집 목록과 pool의 실제 global full·eligible 목록을 구분합니다. 같은 run/attempt/head/checkout/tree/workflow/policy/interpreter/invocation과 pool 4-worker inventory 일치, 전체 critical parameter 집합 일치·실행 union·빈 intersection을 확인합니다. 누락·중복·stale barrier·불완전 lifecycle·구형 v1은 실패합니다. Producer는 다운로드한 원본 receipt와 digest를 재검증하고 consumer는 authenticated manifest의 proof와 기존 trusted-base/tree 계약을 확인합니다. Outer manifest8/profile6 형식은 유지하며 구형 실행 증거는 교환하지 않습니다.
- 각 phase의 `checkpoints-<worker>.json`은 진단 전용이며 proof에서 읽지 않습니다. Identity 검증 뒤 process별 monotonic 기준으로 configure·session·collection(import/selection 포함)·첫 test·stage 시작/완료를 기록합니다. 미관측 시각은 생략하고 report 수는 PASS 수와 구분합니다. Critical stage는 즉시, pool은 최초 stage 뒤 마지막 쓰기에서 5초 이상 지난 hook에서 갱신합니다. Hook이 멈추면 마지막 snapshot만 남습니다. Plugin 이전 비용·worker 간 시간 합산·종료 직전 누락은 추정하지 않습니다.
- process별 `diagnostics-<worker>.json`(`ci-stage-diagnostics-v1`)도 진단 전용(`diagnostic_only=true`, `reusable=false`)이며, proof·metrics·receipt·재사용 판정에서 읽지 않습니다. 모든 profile에서 실패 stage를 보고 즉시 남겨, step timeout으로 master 집계와 FAILURES 출력이 없어도 근거가 남게 합니다.
  - 실패 stage마다 nodeid·stage·outcome·duration, 예외 kind, pytest crash 메시지 첫 줄(앞 4096자만 검사하고 300자만 기록, 제어문자 제거)을 기록합니다. `check_public.sh`가 막는 credential 형식(GitHub·`sk-`·`AKIA`·`AIza`·`xox*`·private key)과 Bearer 값은 고정 패턴으로 가립니다. traceback과 captured output은 넣지 않습니다.
  - 가장 느린 stage 30개와 stage별 시간도 함께 기록합니다.
  - 상한: 실패 50건·nodeid 512자·0~86400초의 유한 duration. 중복 판정은 잘리기 전 원본 nodeid의 sha256으로 하므로, 앞 512자가 같은 긴 parameter도 따로 남습니다. 같은 stage의 중복 보고는 한 번만 셉니다.
  - 파일은 통째로 교체(`os.replace`)합니다. 그 밖의 갱신은 5초 간격이고 session 종료 때 `session_finished`를 남깁니다. 쓰기나 기록이 실패해도 test 결과는 바뀌지 않습니다. #837의 실제 실패 원인은 이 기록으로 다음 run에서 확인해야 하며, 아직 미확인입니다.
- 범위/checkout/ordered parents가 없거나 틀리면 전수로 돌아가지 않고 nonzero로 종료해 진단 JSON을 남깁니다. 미등록 test·mixed source pin 만료·필수 cohort 누락도 빠르게 실패하므로 해당 PR에서 inventory/계약을 검토해야 합니다. 일반 문서만의 skip 판정은 그대로입니다.
- PR event base가 synthetic merge의 첫 parent보다 뒤처지면 head 일치와 forward ancestry를 검증한 뒤 실제 첫 parent부터 선택하며, artifact reuse는 별도 엄격 검사로 판정합니다.
- controller의 원래 operator HOME 신원과 worker별 HOME/state/cache/DB/log 격리는 유지합니다. 전용 subprocess·임의 포트·사용하지 않는 test helper import는 전역 lock 근거가 아닙니다. 참조된 helper/fixture·import-time 실행·별칭을 따라 고정/미확정 listener 포트와 전역 process scan을 보호합니다. 실제 Runner broker의 mutable port가 입증되지 않은 경우도 잠급니다. lock은 setup~teardown을 감싸며 정적 분석의 완전한 동적 증명을 뜻하지 않습니다. production fsync·SQLite FULL·atomic replace·Job은 바꾸지 않습니다.
- 각 CI job artifact에는 full/selected node 목록·제외 이유, CPU·실제 worker 수·platform, setup/pytest/job queue 시간, 실패/skip 수·최장 파일·host-resource lock 대기가 남습니다. worker collection 목록은 서로 같아야 합니다. 3.10 controller 진단과 25분 상한을 유지하고 worker도 5분마다 node/thread/task stack을 별도 기록합니다.
- v8 manifest/artifact는 gate별 profile·policy blob·tree·run/attempt·workflow·digest·ordered parents와 모든 성공 gate를 결합합니다. main push before도 검증된 PR base와 같아야 변경 test 집합을 재사용합니다. Windows full·v1 boundary·구형 manifest·다른 증거는 교환하지 않습니다. 바뀐 정책의 자기 인증은 허용하지 않습니다. 첫 main은 필수 gate를 다시 실행하며 routine Windows를 전수로 확대하지 않습니다. 확인된 snapshot drift의 no-reuse 결과는 아래처럼 별도로 처리합니다.
- 정책 변경 PR도 routine Windows 선택 노드마다 한 번만 실행합니다. 종전 full+preview 이중 실행은 폐기했습니다. `ci-full-v8`은 필수 gate 집합의 이름이며 Windows 전수 증거가 아닙니다. consumer는 다른 tree·policy·profile·run/attempt/digest·ordered parents를 계속 거부하고 full/구형/preview Windows receipt를 boundary 대신 받지 않습니다. 실제 job 최종 시간은 upload가 끝난 GitHub job metadata로 확인합니다.

근거: [xdist distribution](https://pytest-xdist.readthedocs.io/en/stable/distribution.html), [worker isolation](https://pytest-xdist.readthedocs.io/en/stable/how-to.html), [3.8.0 Python >=3.9](https://pypi.org/project/pytest-xdist/3.8.0/). 실제 병렬 실행·Windows 시간의 확인 범위는 [측정 기록](ci-windows-cohort-823.md#실제-ci-측정)에 남깁니다. main의 동일 tree 재사용 성공은 별도 증거가 필요합니다.

run 39461236144에서는 event·run association·현재 PR의 base가 `d0c834a`, 검증 merge의 첫 parent와 main ref가 `2ce8778`인 snapshot drift를 확인했습니다. GitHub 갱신 시점과 이전 run의 원인은 미확정입니다. 같은 repo/run/attempt/workflow·9개 성공 job·동일 tree/순서 있는 merge parents·PR merge SHA/head·main ref·완전한 forward compare를 독립 확인한 경우만 producer가 `reusable=false`로 종료합니다. event base를 바꾸거나 재사용 manifest를 쓰지 않으며 upload도 하지 않습니다. consumer는 artifact가 없으면 full입니다. API 증거가 없거나 다르거나 다른 manifest 오류가 있으면 기존 실패를 유지합니다. 안정된 binding의 manifest 형식과 consumer의 기존 trusted-base policy·digest·identity 검사는 그대로입니다.

Concurrency는 workflow와 PR 번호로 묶어 PR 후속 head만 이전 실행을 취소합니다. main·dispatch는 run/attempt별 group으로 분리해 대기 중 실행도 서로 취소하지 않습니다. #787의 3.10 25분 제한·faulthandler·stall snapshot은 그대로입니다.

runner 경로는 첫 step의 env에서 받아 UTF-8 GITHUB_ENV로 전달합니다. job.env에서는 runner context를 쓸 수 없습니다([context availability](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability)).

GitHub 계약: [concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency), [PR merge SHA](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request), [workflow SHA](https://docs.github.com/en/actions/reference/workflows-and-actions/variables), [attempt jobs](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt), [artifact digest·download](https://docs.github.com/en/rest/actions/artifacts).
