# HPC 단계 workspace (#578)

구현·mock 검증 기록. 실제 HPC·계정 전환·OS 격리는 UNVERIFIED다.

| 경로 | 보관하는 것 |
|---|---|
| `hpc/<request>/steps/<step>/` | 단계별 manifest·jobs·outputs·wake run |
| `hpc/<request>/data/<producer>/<source-task>/` | 승인된 조상 산출의 hash 검증 복사본 |
| `hpc/<request>/.labhq/hpc-inputs/<producer>/<source-task>.json` | 생산 단계·원래 경로·hash |
| `requests/<request>/` | 원본과 분리된 요청 묶음 |

- 서로 다른 단계의 동명 산출은 허용한다. 같은 단계의 required·optional 파일은 equality·ancestor·대소문자 충돌을 거부한다.
- 같은 단계·revision의 성공 잡이 같은 파일을 내면 제출 순서의 마지막 산출을 채택한다. 이전 로컬 사본과 소유 잡 관계는 `jobs/replaced/`와 job 상태에 남긴다. 역순 fetch와 release에서도 살아 있는 산출을 보존한다.
- 실행 중 잡·마지막 성공 뒤 실패·파일/폴더 충돌은 거부한다. 동시 제출, 대소문자만 다른 경로와 제출 순서가 같은 충돌도 거부한다. 새 잡은 기록 순번을 쓰며 구형 잡은 저장 시각을 쓴다. 기존 소유 관계를 판별할 수 없는 중첩 기록은 안전하게 거부한다.
- 새 단계는 기존 요청 공유 폴더를 채택하지 않는다. 구형 원본·잡 기록은 보존한다.
- 앞 단계 입력은 기존 `stage_inputs`의 요청·task·step·성공·hash·용량·통제/개인 경로 검사를 먼저 거친다. 거부하면 원본 경로로 우회하지 않는다. 전달한 파일만 요청 data에 복사하며 task별 버전은 서로 덮지 않는다.
- 공유 계정은 data 복사본만 읽고 단계 outputs에 쓴다. 입력 symlink·hardlink 거부는 유지한다. 운영자가 workspace 위쪽 경로의 traverse 권한을 준비한다.
- 잡 제출 전 baseline을 run에 저장한다. 같은 요청·step·revision의 wake는 첫 제출 baseline을 이어받아 대기 중 생긴 보조 파일도 기록한다. revision이 바뀌면 새 baseline을 쓴다.
- 재개 accounting은 session·runner·engine에 요청·step·revision을 함께 대조한다. verify는 절대 경로가 사라졌을 때 task가 일치하는 유일한 중첩 manifest만 찾는다.
- bundle은 해당 run의 engine/model을 우선한다. 단계 절대 링크와 기록된 data snapshot 링크를 실제 묶음 파일로 바꾼다. model은 RO-Crate software identity hash에 반영된다.
- HPC 환경 binding과 예약된 환경 lock 단계는 검증된 scheduler용 snapshot이 없어 dispatch 전에 거부한다. runner도 binding이 든 HPC task를 비용 없이 거부한다. 기존 local 환경 계약은 유지한다.
- HPC 선택·계획 검증은 요청별 roster와 runner view를 따른다. WSL에 pin된 요청이 native owner의 capability를 쓰지 않는다.
- 요청 project/path-reference 입력은 선택 runner가 briefing 전 무모델 preflight로 검사한다. PI 승인 hash와 원본을 다시 대조해 단계 snapshot을 만들고, 전송본·제출 전 원본과 snapshot을 검증한다. 불일치·누락·검증 실패는 중단하며 직원에 설정된 project 입력은 계속 거부한다.
- 저장된 HPC 선택도 `run_request` 입구에서 새 preflight를 실행한다. 복구 시 preflight 결과는 재사용하지 않으며 일반 완료 처리 작업의 복구는 유지한다. Slurm 고정 후보에는 `--nodes=1`을 함께 지정하고 충돌 옵션과 cluster 환경변수를 거부한다.
- runner는 configured project 입력이 없는 내장 scheduler 직원만 `whole_request_hpc: true`로 알린다. 경로는 roster에 내보내지 않는다. gateway는 이 capability가 true인 직원만 선택·계획에 허용한다. 일반 HPC 도구 지원 여부는 별도이며 capability 누락을 지원으로 추정하지 않는다. 관련 회귀 340 passed/7 skipped.
- 마지막 eligibility 변경 뒤 실제 gateway 왕복 mock E2E 13 passed. 실제 모델·HPC 계정 전환은 실행하지 않았다.

## 04장 반영 (rev3)

`04-install.md`·`.en.md`에 실행 위치 질문·direct 대상·선택 기록·HPC/local 표를 반영했다. 단계별 원본·검증 data 복사·snapshot 미지원·실제 HPC 미검증을 함께 설명한다. WSL 절의 미래형 안내는 현재 절을 가리킨다.
한영 합계 54,037→56,966자(+2,929, 편집 전 `ff2408b6` 기준). 관련 workspace·bundle·verify·HPC mock 회귀 298 passed/25 skipped. #1280 병합 뒤 main 통합과 최종 head 검증은 별도로 추적한다.

## 회귀 범위

CLI `1: a/b`, 실제 worker만 HPC 선택, 동명 산출·파일 경로 충돌, mock 제출·입력 provenance, wake·runner 재시작·revision 분리, 구형 원본 보존, bundle 링크·run identity, 이동 후 verify·오귀속 거부를 검사한다.

rev2 로컬: 확장 993 passed/39 skipped, 후속 bundle·accounting·CSO 413/7, 경계 138/4(서로 중복 포함). Windows의 POSIX 권한·링크 검사는 skip이며 Linux CI가 맡는다.
리뷰 후 환경·WSL runner 회귀 401 passed/15 skipped, 요청 입력·환경·WSL·workspace 회귀 455/33, E2E·resume accounting 39 passed(중복 포함). 08·11장 한영은 합계 158,935→160,187자(+1,252). 경로·검증 상세는 이 reference에 뒀다.

## CI 범위 추가

rev4 실제 head 리뷰 P1 `4479233491`: 저장된 precedent 본문은 scheduler snapshot 계약이 없어 gateway와 runner에서 처리 전에 거부한다. local 선택은 유지하며 자동 전환하지 않는다. 별도 계정에서 step-local 본문을 읽거나 직원 모델이 대신 처리하는 경로를 막는다. 실제 HPC 본문 전달은 미지원이다.

rev4는 `cc7d9087`을 보존하며 main `6323fff5`(`#1281` 포함)를 통합했다. HPC test 등록은 반영됐으나 `test_evidence_verify.py` mixed-node hash·새 node가 남아 inventory 사전 검사는 stale hash로 실패했다. scratch 수정안은 source/AST 검증을 통과했다. policy는 rev4의 34경로 밖이라 편집하지 않았다. 후속 claim이 필요하며 hold를 유지한다. 관련 회귀 298 passed/25 skipped, public·integrations·notes-index·diff 검사 통과. 최종 head CI·실제 리뷰 근거는 PR에 남긴다.
