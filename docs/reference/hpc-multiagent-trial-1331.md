# HPC 다중 직원 실연구(#1331): 실행 절차

airway(GSE52778) RNA-seq를 원시 FASTQ부터 DE까지 PI 인스턴스에서 한 번 돌리고, 통과 기준을 스크립트로 판정합니다.
사이트의 호스트·계정·노드·경로는 이 문서와 공개 기록에 적지 않습니다. 로컬 사이트 프로필에만 있습니다.

| 파일 | 내용 |
|---|---|
| `tools/trial/airway_1331_request.md` | 요청문. CSO가 데이터 받기와 참조 index를 동시 단계로 나누게 씁니다 |
| `tools/trial/judge_1331.py` | 판정. `--watch`로 실행 중 단계 기록, 끝나면 6개 검사 |
| `tools/trial/test_judge_1331.py` | 판정의 합성 데이터 시험(CI 대상 아님) |

## 시작 조건
1. #1295 D단계 통과: 실제 사이트 접속, 60분 창 로그인 상한, 노드 고정.
2. #1332 병합: 동시에 도는 직원 단계가 HPC 세션 하나를 공유.
3. PI 인스턴스를 두 PR이 들어간 main으로 다시 띄웁니다(`tools/lead/instances.ps1 -Only pi`, 로컬 운영 도구). `labhq status`에서 runner 연결, 진행 중 요청 0을 확인합니다.
4. 로컬 설정: `hpc.site_profile`이 PI가 정한 노드·scratch를 가리키고, `hpc.login_limit_*`가 있습니다. `labhq hpc-logins`로 오늘 사용량을 봅니다.

## 실행
1. 보낸다: `labhq -c <config> send "$(cat tools/trial/airway_1331_request.md)" --team --work-kind research`. 요청 id를 적습니다.
2. 곧바로 감시를 띄웁니다(분리 실행, 요청이 끝나면 스스로 멈춤):
   `python tools/trial/judge_1331.py --config <config> --request <id> --watch --events-log <로컬>/events-<id>.jsonl`
   - gateway의 `events` 표는 최근 2000건만 남기고, 요청별 보관표(`messenger_events`)에는 단계 시작·끝이 없습니다. 감시 기록 없이는 긴 실행의 겹침을 판정할 수 없습니다.
3. 승인 카드는 원 명령을 읽고 대리 승인합니다. 지정 노드·scratch 밖을 가리키거나 LLM 설치·로그인을 늘리는 명령은 거절합니다.
4. 실패하면 원인을 #1331에 적은 뒤에만 다시 시도합니다. 시도 번호·원인·바꾼 것을 아래 표에 한 줄씩 남깁니다(최대 20회).

## 판정
`python tools/trial/judge_1331.py --config <config> --request <id> --events-log <같은 파일> --json <로컬>/judge-<id>.json`

| 검사 | 기준 | 근거 |
|---|---|---|
| genes | DUSP1·KLF15·PER1·TSC22D3·ZBTB16·FKBP5 중 5개 이상 log2FC>0, padj<0.05. 표는 1만 행 이상 | 요청 묶음의 DE 표(`--de`로 지정 가능) |
| overlap | HPC 도구를 쓴 서로 다른 단계 둘 이상이 겹침 | 단계 시작·끝 이벤트 순서 |
| session | 겹친 동안 로그인 1회 이하 | `hpc_logins.sqlite3` |
| window | 어느 60분 구간에도 로그인 2회 이하 | 같은 기록, 요청 전후 1시간 포함 |
| verify | `labhq verify` exit 0 | |
| public | 사이트 용어가 보고서·묶음 텍스트에 없음 | 설정·사이트 프로필에서 용어를 뽑고 출력하지 않음 |

판정은 읽기 전용입니다. 저장소 공개 검사(`scripts/check_public.sh`)는 PR마다 따로 돕니다.
"HPC를 쓴 단계"는 그 단계의 로그·승인에 `hpc_*` 도구 이름이 나온 경우입니다. 판정이 고른 단계 목록을 결과 JSON의 `hpc_steps`에서 눈으로 확인합니다. 빠진 단계가 있으면 `--hpc-step <step_id>`로 지정하고, 그 사실을 보고에 적습니다.

## 시도 기록
| 회 | 일시 | 요청 | 결과 | 원인·바꾼 것 |
|---|---|---|---|---|
