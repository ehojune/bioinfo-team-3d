# 외부 감사 반영 — 조회용 근거

현재 큐는 [#739](https://github.com/ehojune/bioinfo-team-3d/issues/739), PI 결정은 [#760](https://github.com/ehojune/bioinfo-team-3d/issues/760#issuecomment-6281989709)가 정본이다.

| 과제 | 확인·완료 경계 |
|---|---|
| #794 | #789 병합 뒤에도 실행의 `-S`가 환경 site-packages를 막음. 실행 플래그와 문법 검사를 분리하고 sibling·venv site·caller/PYTHONPATH/user-site 격리를 실제 subprocess CI에서 확인 |
| #795 | gateway 연결 불가를 확인했으나 Job Object 원인은 가설. Job 소속·limits·종료와 생존 근거 필요. breakaway 거부의 조용한 fallback 금지. pristine/정상 down/원격과 비정상 missing·stale의 runtime 판정 구분 |
| #796 | CP1 전에 input-fit 진단·보정, 기존 보고서에 최소 기여 지표. 건수/전후 trace와 과학 성능의 인과 효과를 구분. Shadow 유지·확장 동결, 효용 판정 뒤 관측 코드 정리 |
| #797 | CP1 전 재사용 snapshot을 승인에 묶고 승인 후 변경은 중단·재승인. 노드 hash는 과학적 입력·의존 산출·실행 명세·pack 의미를 보존 |
| #798 | async 함수의 CP1 전 순차 대기를 해소. 승인 plan hash는 유지하고 build/참조 receipt를 runtime에 분리. 장착·실제 참조·효용을 구분하며 관측 누락을 false로 바꾸지 않음 |
| #799 | 기존 `bioinfo_policy.py`의 응집 규칙 이행과 handoff 비용을 측정. 네 arm·독립 review·CP2 유지 |

실제 프로세스 통합 검사는 #794·#795의 임시 state/fake child를 쓰는 격리 CI 범위다. 로컬 mock guard를 해제하거나 PI 인스턴스·연구 요청을 검사 fixture로 쓰지 않는다. 작업자는 지정 모델·정확한 세션을 재사용하며 같은 파일 수정은 순차 처리한다.

144분/19분과 21:26은 [벤치 A 2차](https://github.com/ehojune/bioinfo-team-3d/issues/373#issuecomment-5975156477), 22:28은 [벤치 C t6 재채점](https://github.com/ehojune/bioinfo-team-3d/issues/373#issuecomment-5999676070)이다. C의 [원 보고 시간은 85.9분](https://github.com/ehojune/bioinfo-team-3d/issues/373#issuecomment-5993720748)이다. 서로 다른 회차를 묶지 않으며 과분할의 인과 기여는 미측정이다.

## 2026-10-20 갱신 (문서 정비 #801)

위 표는 감사 당시의 경계로 둡니다. 같은 날 진행은 다음과 같습니다.

| 과제 | 상태 |
|---|---|
| #794 | PR #800으로 병합(`28a25fba`). 실행 `-E -s -B`, 문법 검사 `-I -S -B`, 실제 interpreter CI test 1개. 같은 PR에서 Windows tree-kill의 PID 재사용 고아 판정과 외부 조회 progress 종료(Python 3.10)도 고침 |
| #796 | PR #803으로 병합(`9acf873b`). CP1 전 기존 검증에 입력 불일치 진단·교정 연결, 보고서에 근거·미측정 구분. 효용은 미측정 |
| #795 | PR #802 열림(병합 전) |
| #797 | PR #807 열림(병합 전) |
| #798 · #799 | 보류(PI가 접수를 다시 열 때까지) |

열린 PR이 병합되면 manual의 해당 절과 HANDOFF 표를 그때 갱신합니다.
