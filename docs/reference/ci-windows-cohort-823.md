# Windows cohort 후속 (#823 · 미게시)

OS 경계와 변경 test를 선택하는 제안입니다. Windows full과 동등하지 않으며 실제 속도는 미측정입니다.

## 현재 변경안: routine Windows 10분 (#825)

- PR/main은 필수 file/node와 변경된 Windows 관련 test만 한 번 실행합니다. 정책 변경도 full+preview로 확대하지 않습니다. Linux 3.10/3.12 전수·Windows nightly/manual 전수는 유지합니다.
- profile schema5/boundary-v5·manifest7입니다. 알려진 web shutdown·WMI escape/ACK 5개 callable 누락, 잘못된 range·미등록 test·만료 pin은 실패이며 전수 fallback이나 false-green이 아닙니다. 실제 native assertions·fsync/SQLite·host lock은 바꾸지 않습니다.
- routine job10분/test step7분으로 report/upload 여유를 둡니다. timeout은 실패이며 완료 시간을 줄였다는 측정이 아닙니다. hard job 취소 시 마지막 업로드가 남지 않을 위험도 있습니다.
- 기존 cached corpus에 대한 변경 없는 range 가정은 **3,252/10,762(30.2%)**, 7,510개 제외입니다. 예전 4,716개와 달리 generic schema/research/Shadow·미변경 비필수 node는 Linux/nightly에 남깁니다. 새 native parameter와 실제 PR 변경 test는 이 과거 분모에 없는 추가량입니다. 10분 성공·실제 selected count·host wait/job 시간은 root 통합 head CI에서 확인해야 합니다.
- [현재 선택·증거 계약](ci-reuse.md)을 따릅니다. 아래 phase4 수치와 full+preview 설명은 이전 제안의 보존 기록이며 현재 실행 정책이 아닙니다.

## 이전 phase4·preview 근거 (대체됨)

| phase4 정적 collection/선택 (`c29e2e3a`) | 값 |
|---|---|
| 이번: 변경 test 없는 유효 range 가정 | 4,716/10,762개(43.8%), 175파일; 6,046개 제외 |
| 현재 local main diff의 변경 test 포함 예상 | 5,114개; 실제 GitHub event 증거 아님 |
| 이번 검토가 유지한 명시 경계 | cohort 3,046 + mixed 819 = 3,865개(35.9%) |
| host lock | 139노드/23파일, 모두 선택; 직전과 같음 |

**30%·2–3천 개 목표는 미달입니다.** 3,865개는 이번 검토의 보호 범위이며 보편적 최솟값을 증명하지 않습니다. collection 목록 밖인 CI 회귀 151개와 main의 새 metrics 18개는 모두 포함합니다.

- paper-skill sandbox/private/namespace/link/hash 8개 누락을 명시 cohort로 복구했습니다. 설치 497→462(인용 Bash 데이터·비설치 인수 35개 제외), policy 256→250(fake clock·수치 6개 제외), web_3d 93→92(JS 문자열 1개 제외)입니다. 실제 installer/PowerShell·junction/경로·security·browser/Node·packaging 경계는 유지합니다.
- init 104개는 유지합니다. defaults의 순수 body 후보도 autouse HOME/경로·doctor 교체 fixture를 거칩니다. fixture 우회나 추가 추론 규칙 없이 줄일 근거는 확보하지 못했습니다. policy_scan_cost 31개와 나머지 web 46개도 유지합니다.
- [profile](../../config/ci/windows-boundary-v1.json)은 schema 4 / `windows-boundary-v4`입니다. 혼합 source digest가 다르거나 node를 해석하지 못하면 포함합니다. native test/helper·fixture·import-time backstop, 변경 test·미검토 파일 포함, 잘못된 range·공유 fixture/dependency의 full fallback은 유지합니다. Linux 3.10/3.12와 Windows nightly/manual은 전수입니다.
- host lock의 scoped helper·고정/미확정 listener·global snapshot 보호와 setup~teardown 범위는 변경하지 않았습니다. 지역 포트 0·전용 subprocess는 자동 전역 lock 사유가 아닙니다. production fsync·SQLite·Job 로직은 바꾸지 않았습니다.
- manifest/artifact v6는 v4 profile과 기존 tree/workflow/policy/run/attempt/digest/ordered parents·trusted base·성공 gate를 묶습니다. 구형/preview 증거는 교환하지 않습니다.

## 정책 변경 PR의 측정

필수 Windows full 성공 뒤 같은 job에서 preview만 추가합니다. 일반 PR/main·nightly/dispatch는 중복 실행하지 않습니다. preview는 same-repository PR의 실제 checkout·ordered base/head·workflow SHA/ref와 실제 변경 경로를 검증합니다. 좁은 CI policy 변경만 예외로 선택하며 공유 fixture/dependency 변경·불명확한 range에는 실행하지 않습니다.

`windows-boundary-preview-v4`·`preview-changes.json`·`boundary-preview-*`·`preview-summary.json`으로 분리하고 `reusable=false`를 기록합니다. full gate output/manifest upload는 그대로이며 producer/consumer가 preview를 거부합니다. full/selected/excluded·workers·host wait·setup/pytest/queue를 따로 보고합니다. 같은 job의 warm 후속 실행이므로 cold PR 성능과 같지 않습니다. preview 실패도 job 실패이며 숨기지 않습니다.

main `56914c7b`(PR824 `8baefd07` 포함)를 충돌 없이 로컬 통합했습니다. S1/제품/README/manual은 main과 같습니다. phase3 보호·preview와 기존 gate는 유지했습니다. 새 reuse 계약 포함 mock/static 387 PASS, 추가 4개 포함 마지막 fake subset 91 PASS입니다. main 변경과 새 계약 200개만 serial collection하고 나머지는 재사용했습니다. 실제 Windows/xdist·경합·5분 달성은 미검증이며 총괄의 exact-head 검토·게시·원격 CI가 남습니다.

run 39538379069의 REST association은 빈 목록이었고 main 39541901246은 discovery에서 full로 갔습니다. 추가 경로는 authoritative merged PR 재조회와 현재 epoch의 모든 manifest/gate/tree/policy/ordered-parent/digest 증거를 요구합니다. 제공된 v3 artifact는 v6 정책 인증에 쓰지 않습니다. 실제 downstream 증거 전체를 재조회하거나 새 재사용 성공을 확인하지 않았습니다.

## PR825 초기 비용 검사 기록 (source 4128943a)

run39544266122/job119038816204는 sanitizer 원문 보존을 통과하고 wall 2.092초로 `<2`만 실패했습니다(4 workers, 10,724 PASS/43 skip). 이 separator 입력의 전진 scan·run 시작점 처리는 확인했지만 초선형 결함이나 경합 원인을 입증한 것은 아닙니다. CI CPU 값은 없습니다.

제품은 바꾸지 않고 같은 731,072자·limit=None·출력·엄격한 2초 조건을 process CPU로 잽니다. module-local fake clock은 wall900/CPU0.125 허용, CPU2 이상 거부, 출력 변경 거부를 확인합니다. red3→green16 순수 문자열 검사이며 수학적 선형성이나 새 CI 통과의 증명은 아닙니다. 위 cohort/측정 자료는 재실행하지 않은 phase4 snapshot이며 이번 fake case 5개는 포함하지 않습니다. Windows/S1 결과는 별도입니다.
## PR825 실제 CPU 초과 후 제품 수정 (source ea6a8731)

후속 run39548511101/job119052128054는 출력 보존을 통과했지만 **process CPU 2.329초**로 실패했습니다(10,740 PASS/43 skip). wall만의 문제라는 설명은 성립하지 않습니다. 이 입력의 초선형 경로는 입증하지 못했으며 로컬 profile은 공용 path scanner·credential regex·Python 구분자 순회를 비용으로 확인했습니다.

실제 sanitizer인 `labhq/integrations/github.py`에서 URL 패턴의 필수 문자 `@`·`.`·`=`가 없을 때만 불가능한 탐색을 생략하고, literal path 구분자 run을 고정 문자 클래스 search로 전진시킵니다. 정규식/판정 순서·공용 scanner·경계·출력은 유지합니다. deterministic work 회귀 red5/기존 결과 비교2 PASS → 관련 pure 24 PASS, 경로 1,584·URL 180조합 동등, 원래 test/helper AST 31개 동일입니다.

같은 Windows lane Python3.12의 731,072자 입력 CPU는 원본 0.156초×2 → 수정 0.078초×2였습니다(15.625ms clock 해상도). strict CPU<2·limit=None·출력 assertion은 그대로입니다. 이 관측은 Linux CI 실패 재현·임의 입력의 선형성 증명·새 head CI 성공이 아닙니다. shared scanner 비용은 남습니다. Python3.10 AST·public·기록/diff를 검사하며 S1/Windows·selector/workflow/profile·preview/exact-proof는 변경하지 않았습니다. native/process/thread/xdist·실제 연구/bench와 게시 없이 root의 exact-head 검토·CI가 남습니다.

## PR825 worker 진단 회귀와 browser 미해결 경계

- base `6b818f8c`: Linux 3.10/3.12의 실패는 worker `gw0/gw1` 진단을 `master` 파일로 읽던 test 오류입니다. fake master/gw0/gw1에서 missing-native 실패와 정확한 파일·payload를 확인합니다.
- Windows resizable test는 30초 wrapper가 60초 browser test/startup보다 먼저 끝납니다. 자동 Chrome 탐지·desktop/phone `finally close`는 있지만 실제 지연 단계와 정리 완료는 로그로 확정하지 못했습니다. WMI provider timeout은 다른 담당 범위입니다.
- timeout/skip/선택/assertion은 유지합니다. wrapper가 부분 TAP·stderr와 원래 timeout 예외를 보존하고 browser open/load/assertions/close 표식을 남깁니다. **Windows 원인 수정이나 성공 주장이 아닌 진단 보강**이며 다음 조치는 root 검토가 필요합니다.
- fake/static 50 PASS; worker red 2 FAIL/1 PASS, 진단 red 3 FAIL/3 PASS 로그를 보존했습니다. JS body는 표식·공백 외 동일, Python assertions와 선택 계약은 동일하며 exact source pin만 갱신했습니다. 실제 Node/browser/xdist는 로컬 실행하지 않았습니다.
- 실제 Windows 435초/pytest376.60초는 2 FAIL이므로 10분 목표 성공이 아닙니다. Linux full·Windows 필수 native/nightly full·10분 상한과 proof/manifest 계약은 그대로입니다.


## PR825 critical phase 격리 (미게시)

- 제공된 독립 원격 probe에서 같은 제품/native blob의 두 파일은 25 PASS/0 SKIP, pytest36.053초였습니다. Browser는 원래30초 wrapper에서 desktop/phone 모두 완료, Node3 PASS/0 SKIP·9.609초였습니다. Probe는 제품/PI 성공이나 경합 원인의 증명이 아닙니다.
- 실행 v2는 두 native 파일 전체+정확한 browser node만 serial 수집합니다. 현재26개이며 추가 native case도 자동 포함합니다. 필터 없는 명시 인수와 모든 lifecycle PASS/no-SKIP 뒤 pool을 엽니다. Critical은 좁은 collected/selected만 기록하며 global full/eligible은 실제 pool 수집에서 가져옵니다. 전체 critical parameter 집합·union·빈 intersection·worker/controller report·시간 순서·동일 invocation을 검증합니다. Browser의 내부 Node3 PASS/0 SKIP 계약은 유지합니다.
- Profile6/outer manifest8은 helper까지 policy blob에 묶고 execution은 `windows-critical-serial-v2`로 구분합니다. Producer는 원본 phase JSON·job output digest를 재검증하며 consumer도 bound attestation을 요구합니다. v1·다른 tree/policy·partial/failed/skipped critical은 재사용하지 않습니다. 새 offline v7 reader도 Windows 측정 전용이며 reusable=false입니다.
- Linux 전수와 Windows nightly/manual 전수를 유지합니다. Routine10분 hard bound 안에서 serial2분/parallel7분을 배정하며 timeout은 실패입니다. 실제 f0 CI는 critical26 PASS였지만 global collection83.234초가 중복됐고 pool 종료가 불완전해 job570초 FAIL입니다. 좁힌 수집의 실제 절감·새 head의 성공≤600초는 미검증이며 제품/native/browser timeout은 그대로입니다.
