# HPC 다중 직원 실연구(#1331): scDRS 재현 실행 절차

scDRS(Zhang et al. 2022 Nat Genet)의 TMS FACS 세포 유형-질병 연관을 PI 인스턴스에서 HPC로 재현하고, 통과 기준을 스크립트로 판정합니다.
사이트의 호스트·계정·노드·경로는 이 문서와 공개 기록에 적지 않습니다. 로컬 사이트 프로필에만 있습니다.

| 파일 | 내용 |
|---|---|
| `tools/trial/scdrs_1331_request.md` | 요청문. Paper2Agent 채용, 데이터 받기 ∥ 환경 준비(둘 다 HPC), 파견직의 점수 계산·검정 |
| `tools/trial/scdrs_1331_table.tsv` | 판정표. 실행 전에 고정했고 실행 뒤 고치지 않습니다 |
| `tools/trial/judge_1331.py` | 판정. `--watch`로 실행 중 단계 기록, 끝나면 7개 검사 |
| `tools/trial/test_judge_1331.py` | 판정의 합성 데이터 시험(CI 대상 아님) |

## 판정표
- 출처: figshare `scDRS_data_release_030122`(doi:10.6084/m9.figshare.19312583)의 `supp_table.zip`(md5 `62798c2c…`) 안 `supp_tables.xlsx`, 시트 `ST14 Results for TMS FACS`. 120 세포 유형 × 74 형질 = 8,880쌍입니다. 논문 본문은 이 결과를 Supplementary Table 12, Figure 3, Extended Data Figure 3으로 인용합니다.
- **연관 19쌍**: 논문 FDR 0.019(MC 검정의 최소값). 본문이 "잘 알려진 연관"으로 든 범주에서 골랐습니다. 면역 질환-CD4/CD8 T 세포 7쌍, MS-GMP, 혈액 형질-혈액 세포 2쌍, 지질 형질-간세포 3쌍, 뇌 관련 형질-neuron 3쌍, 키-연골세포, 혈당-췌장 β 세포, 수축기 혈압-심실 근세포.
- **비연관 5쌍**: 논문 FDR ≥ 0.59, 블록 밖 짝. TG-CD4 T, IBD-간세포, 키-neuron, LDL-neuron, SCZ-CD4 T.
- 통과: 연관 15/19 이상이 q<0.05, 비연관 중 q<0.05가 1쌍 이하. q는 판정이 실행 결과의 `assoc_mcp` 전체에 BH를 다시 적용해 구합니다. 직원이 계산한 FDR은 참고만 합니다. 형질이 일부만 돌면 BH 규모가 달라지므로 쌍 7,000개 미만이면 실패입니다.

## 데이터와 계산 규모
| 파일 | 크기 | 쓰임 |
|---|---|---|
| `tabula_muris_senis.zip` | 997.5 MB(md5 `0a56be58…`) | TMS FACS 자료·공변량 |
| `gs_file.zip` | 0.9 MB(md5 `14434666…`) | 논문의 MAGMA 유전자 세트 74개 |
| `score_file.zip`·`downstream_file.zip` | 2.0 GB·17 MB | 저자 결과. **받지 않음**(요청문에서 금지, 리뷰가 확인) |

- 저자 배포본의 형질별 대조 점수 파일은 압축 상태로 형질당 약 523 MB입니다. 74개를 다 남기면 압축으로도 약 39 GB입니다. 요청문은 검정 뒤 삭제를 허용합니다.
- 받는 위치는 figshare `ndownloader.figshare.com`입니다. 노드에서 외부망이 안 되면 데이터 단계가 실패하므로, 첫 시도에서 그 원인을 확인해 issue에 적습니다.

## 시작 조건
1. #1295 D단계 통과: 실제 사이트 접속, 60분 창 로그인 상한, 노드 고정. 현재 PI 설정은 `hpc.scheduler: none`, 사이트 프로필 없음, 로그인 상한 없음입니다. D단계가 이 셋을 채워야 시작합니다.
2. #1332 병합: 동시에 도는 직원 단계가 HPC 세션 하나를 공유.
3. PI 인스턴스를 두 PR이 들어간 main으로 다시 띄웁니다(로컬 운영 도구 `instances.ps1 -Only pi`). `labhq status`에서 runner 연결, 진행 중 요청 0을 확인합니다. `recruit.enabled`가 켜져 있고 doctor의 paper2agent가 ok인지 봅니다.

## 채용과 동시 실행의 순서
labhq는 채용을 **계획 확정 전에** 끝냅니다(`recruit/planning.py`의 `prepare_plan`, CSO의 `prepare_recruits`). 그래서 채용과 데이터 받기는 동시에 돌 수 없습니다. 동시 실행 기준은 채용 뒤의 두 HPC 단계, 곧 데이터 받기와 환경 준비로 채웁니다.

## 실행
1. 보낸다: `labhq -c <config> send "$(cat tools/trial/scdrs_1331_request.md)" --team --work-kind research`. 요청 id를 적습니다.
2. 곧바로 감시를 띄웁니다(분리 실행, 요청이 끝나면 스스로 멈춤):
   `python tools/trial/judge_1331.py --config <config> --request <id> --watch --events-log <로컬>/events-<id>.jsonl`
   - gateway의 `events` 표는 최근 2,000건만 남기고, 요청별 보관표(`messenger_events`)에는 단계 시작·끝이 없습니다. 감시 기록 없이는 긴 실행의 겹침을 판정할 수 없습니다.
3. 승인 카드는 원 명령을 읽고 대리 승인합니다. 지정 노드·scratch 밖을 가리키는 명령, LLM 설치, 로그인을 늘리는 명령, 저자 결과 파일 다운로드는 거절합니다.
4. 실패하면 원인을 #1331에 적은 뒤에만 다시 시도합니다. 시도 번호·원인·바꾼 것을 아래 표에 한 줄씩 남깁니다(최대 20회).

## 판정
`python tools/trial/judge_1331.py --config <config> --request <id> --events-log <같은 파일> --json <로컬>/judge-<id>.json`

| 검사 | 기준 | 근거 |
|---|---|---|
| repro | 판정표 기준(위) | 요청 묶음의 `*.scdrs_group.*` 또는 `celltype_assoc.tsv`(`--assoc`로 지정 가능) |
| hire | scDRS 논문의 활성 파견직이 있고, 그 파견직에 배정된 단계를 그 파견직만 수행 | 요청 상태의 `plan.recruit_results`·`plan.steps`와 단계 기록 |
| overlap | HPC 도구를 쓴 서로 다른 단계 둘 이상이 겹침 | 단계 시작·끝 이벤트 순서 |
| session | 겹친 구간(늦은 시작~이른 끝)에 로그인 1회 이하 | `hpc_logins.sqlite3` |
| window | 어느 60분 구간에도 로그인 2회 이하 | 같은 기록, 요청 전후 1시간 포함 |
| verify | `labhq verify` exit 0 | |
| public | 사이트 용어가 보고서·묶음 텍스트에 없음 | 설정·사이트 프로필(접속 명령의 사용자·호스트 포함)에서 용어를 뽑고 출력하지 않음 |

판정은 읽기 전용입니다. 저장소 공개 검사(`scripts/check_public.sh`)는 PR마다 따로 돕니다.
"HPC를 쓴 단계"는 그 단계의 로그·승인에 `hpc_*` 도구 이름이 나온 경우입니다. 결과 JSON의 `hpc_steps`를 눈으로 확인하고, 빠진 단계는 `--hpc-step <step_id>`로 지정한 뒤 그 사실을 보고에 적습니다.

## 시도 기록
| 회 | 일시 | 요청 | 결과 | 원인·바꾼 것 |
|---|---|---|---|---|
