# Windows cohort 후속 (#823 · 미게시)

OS 경계와 변경 test를 선택하는 제안입니다. Windows full과 동등하지 않으며 실제 속도는 미측정입니다.

| 정적 collection/선택 | 값 |
|---|---|
| 직전 | 4,566/10,578개(43.2%), 171파일 |
| 이번: 변경 test 없는 유효 range 가정 | 4,592/10,638개(43.2%), 173파일; 6,046개 제외 |
| 현재 local main diff의 변경 test 포함 예상 | 4,990개; 실제 GitHub event 증거 아님 |
| 이번 검토가 유지한 명시 경계 | cohort 3,031 + mixed 819 = 3,850개(36.2%) |
| host lock | 139노드/23파일, 모두 선택; 직전과 같음 |

**30%·2–3천 개 목표는 미달입니다.** 3,850개는 이번 검토의 보호 범위이며 보편적 최솟값을 증명하지 않습니다. 새 회귀 60개는 미검토 신규 파일이므로 모두 포함합니다.

- paper-skill sandbox/private/namespace/link/hash 8개 누락을 명시 cohort로 복구했습니다. 설치 497→462(인용 Bash 데이터·비설치 인수 35개 제외), policy 256→250(fake clock·수치 6개 제외), web_3d 93→92(JS 문자열 1개 제외)입니다. 실제 installer/PowerShell·junction/경로·security·browser/Node·packaging 경계는 유지합니다.
- init 104개는 유지합니다. defaults의 순수 body 후보도 autouse HOME/경로·doctor 교체 fixture를 거칩니다. fixture 우회나 추가 추론 규칙 없이 줄일 근거는 확보하지 못했습니다. policy_scan_cost 31개와 나머지 web 46개도 유지합니다.
- [profile](../../config/ci/windows-boundary-v1.json)은 schema 4 / `windows-boundary-v4`입니다. 혼합 source digest가 다르거나 node를 해석하지 못하면 포함합니다. native test/helper·fixture·import-time backstop, 변경 test·미검토 파일 포함, 잘못된 range·공유 fixture/dependency의 full fallback은 유지합니다. Linux 3.10/3.12와 Windows nightly/manual은 전수입니다.
- host lock의 scoped helper·고정/미확정 listener·global snapshot 보호와 setup~teardown 범위는 변경하지 않았습니다. 지역 포트 0·전용 subprocess는 자동 전역 lock 사유가 아닙니다. production fsync·SQLite·Job 로직은 바꾸지 않았습니다.
- manifest/artifact v6는 v4 profile과 기존 tree/workflow/policy/run/attempt/digest/ordered parents·trusted base·성공 gate를 묶습니다. 구형/preview 증거는 교환하지 않습니다.

## 정책 변경 PR의 측정

필수 Windows full 성공 뒤 같은 job에서 preview만 추가합니다. 일반 PR/main·nightly/dispatch는 중복 실행하지 않습니다. preview는 same-repository PR의 실제 checkout·ordered base/head·workflow SHA/ref와 실제 변경 경로를 검증합니다. 좁은 CI policy 변경만 예외로 선택하며 공유 fixture/dependency 변경·불명확한 range에는 실행하지 않습니다.

`windows-boundary-preview-v4`·`preview-changes.json`·`boundary-preview-*`·`preview-summary.json`으로 분리하고 `reusable=false`를 기록합니다. full gate output/manifest upload는 그대로이며 producer/consumer가 preview를 거부합니다. full/selected/excluded·workers·host wait·setup/pytest/queue를 따로 보고합니다. 같은 job의 warm 후속 실행이므로 cold PR 성능과 같지 않습니다. preview 실패도 job 실패이며 숨기지 않습니다.

mock/static 475 PASS, 마지막 소규모 확인은 status에 기록합니다. 바뀐 CI 계약 235개만 재수집하고 나머지 collection은 재사용했습니다. 실제 Windows/xdist·경합·5분 달성은 미검증입니다. #824 이후 총괄의 main 통합·exact-head 검토·게시·원격 CI가 남습니다.
