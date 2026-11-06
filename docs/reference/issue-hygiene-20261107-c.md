# issue 정리 C (2026-11-07)

lookup-only. 기준은 [정리 A](issue-hygiene-20261107-a.md)와 같다. 완료 조건이 main에서 충족됐으면 근거를 달아 닫고, 아니면 남은 일과 gate를 적는다. main `87ea35f5` 기준으로 읽었고, 인스턴스·HPC·WSL은 건드리지 않았다.

| issue | 판정 | 남은 일 |
|---|---|---|
| [#430](https://github.com/ehojune/bioinfo-team-3d/issues/430) 공유 계정 HPC 잡에 앞 단계 산출 전달 | **닫음** | 실제 클러스터 확인은 #1295(D단계)가 갖는다 |
| [#612](https://github.com/ehojune/bioinfo-team-3d/issues/612) 로컬 WSL 분석 | 열어 둠 | 실제 WSL에서 `setup`·nf-core 시험 파이프라인 끝까지 |
| [#151](https://github.com/ehojune/bioinfo-team-3d/issues/151) EDAM 부분집합과 input_fit | 열어 둠 | #99 gate 수치 또는 폐기 기록, 본문 정정, PI 라이선스 확인(#882 동결) |
| [#625](https://github.com/ehojune/bioinfo-team-3d/issues/625) Freda·Moore 랩 조사에서 가져올 것 | 열어 둠 | 추출 캐시·재개, 요인 실험의 실제 실행(승인 필요) |
| [#621](https://github.com/ehojune/bioinfo-team-3d/issues/621) AlphaMissense·AlphaGenome | 열어 둠 | 실제 Atlas 조회(키 위치·PI 확인) |
| [#546](https://github.com/ehojune/bioinfo-team-3d/issues/546) 디자인 리뉴얼 | 열어 둠 | 3D 개편(PI가 나중으로 둠) |

## 닫은 issue

### #430
- 문제: `hpc.submit_prefix` 모드에서 잡이 별도 `hpc.user`로 돌고, 앞 단계 작업 폴더는 private이라 앞 단계 산출을 읽는 HPC 잡이 permission denied로 실패한다. 제안은 산출을 복사(stage)하거나 `job_group` 읽기 권한을 주는 것이었다.
- 결정(PI, 10-13, #435): **HPC가 필요한 요청은 처음부터 끝까지 HPC에서 진행**하고, 요청 시작 때 HPC·로컬을 PI에게 묻는다. 로컬과 HPC가 섞이지 않게 하려는 것이다.
- main: PR #578(`cdd0a570`, 2026-11-05 병합) "HPC 요청 실행 위치를 선택하고 단계별 workspace·입력·기록 보존"이 이 결정을 구현했고, **이 issue의 첫 번째 안(staging)도 함께 구현했다**. 검증된 앞 단계 산출을 `hpc/<request>/data/<producer>/<source-task>/`에 hash 검증 복사본으로 두고 `.labhq/hpc-inputs/<producer>/<source-task>.json`에 생산 단계·원래 경로·hash를 적어 후속 HPC 잡이 읽게 한다. 공유 계정은 data 복사본만 읽고 단계 outputs에 쓰며, 입력 symlink·hardlink 거부와 `stage_inputs`의 요청·task·hash·용량·통제/개인 경로 검사는 유지한다(`docs/reference/hpc-step-workspaces-578.md`, `labhq/runner/workspace.py:557`, `labhq/tools/hpc_mcp.py:143`). PR #429(`inputs/<단계>` 연결)도 병합돼 있다.
- 남은 것은 총괄 댓글이 이미 "실제 실행 확인은 서버 복구 뒤"로 나눠 둔 실제 클러스터 확인이다(공유 계정 전환, 상위 경로 traverse 권한은 운영자가 준비하는 부분이라 실제 클러스터에서만 맞다). 이는 HPC 세션 작업(D단계)에서 다루므로 #1295가 갖는다. 두 번째 안(읽기 권한)은 하지 않는다.

## 열어 둔 issue

### #612
- 완료: 1단계 `labhq setup-wsl`과 doctor WSL 절, 요청 단위 WSL 셋업 카드(PR #1038), 요청에 `run_where: auto|wsl`을 더해 WSL을 고르면 계획 전에 셋업 카드를 여는 것(PR #1189 `5eab2b0c`). `labhq/gateway/server.py:186`의 `RequestIn.run_where`, `labhq send --wsl`(`cli.py`), 웹 요청 폼의 `#wsl-only`(`labhq/web/index.html:990`), CSO 입력의 한 줄이 모두 main에 있다. `tests/test_wsl_setup_card_612.py` 39건을 이번에 돌려 통과했다.
- #578을 다시 쓰는 대신 독립 조각으로 간다는 총괄 결정(10-31)대로이고, 위 조각이 그 구현이다.
- 남은 일(gate): 이슈 본문의 확인 항목인 "작은 nf-core 테스트 파이프라인(`nf-core/demo -profile test,docker`)을 bioinfo-agent 경로로 실제로 끝까지"와, 실제 WSL 배포판에서 gateway가 `setup`을 돌리는 시험이다. 둘 다 실제 WSL 실행이라 PI 승인 뒤에 한다. 셋업 뒤 WSL runner가 붙기까지의 시간과 계획을 막는 대기 상한도 재 보지 않았다.

### #151
- core로 남아 있는 것을 확인했다(총괄 요청 사항): `labhq/vocab/edam_map.yaml`·`edam_subset.yaml`과 `scripts/edam_subset.py`가 main에 있고, `labhq/vocab/__init__.py`가 `edam_subset.yaml`을 선택 어휘로 읽는다(`SUBSET_FILE`, `Vocab.edam_id`). `scripts/semantics_shadow_remove.py`는 출력 종류 어휘를 "shadow보다 오래 남아야 하는 core(#221)"로 두고 제거 대상에서 뺀다. 그래서 EDAM 산출물이 제거 목록에 없는 것은 결함이 아니다(10-30 정정과 같음).
- `tests/test_edam_subset.py`·`test_semantics_input_fit.py`·`test_semantics_shadow_remove.py` 51건을 이번에 돌려 통과했다.
- 남은 일(10-29 대조 댓글과 같다): ① #99 gate(fixture 40·추가 적발 ≥4·18/20·오탐 ≤1)의 수치를 재는 시험과 기록, 또는 PI가 gate를 폐기한다는 기록. #99는 10-01에 닫히면서 정본을 #151로 넘겼으므로 이 gate는 여기에 남아 있다. ② 본문의 `≤40`과 파일 경로를 현재 값(`MAX_KEYS = 92`+topic, `labhq/vocab/`)으로 정정. ③ PI의 EDAM 라이선스 확인 기록. #882 동결 범위다.

### #625
네 항목의 현재 상태:

| 가져올 것 | 상태 | 근거 |
|---|---|---|
| 비용 모름 표시, 호출 없는 dry-run | 완료 | PR #651(`e383acbf`) |
| 단계별 모델 등급과 가격 | 완료(기본 꺼짐) | 1단계 PR #1097(`af4220af`): 등급→모델 표, dry-run 모델 열, 선언 등급과 실제 모델 불일치 경고. 2단계 `c094a2c2`: 계획 단계 `model_tier`의 데이터 경로(꺼진 기본, 직원 등급 이하·validation/report 하한). `orchestrator.step_model_tiers: false`가 기본(`settings.py:522`) |
| 요인 실험 벤치 | 코드 완료, 실제 실행 안 함 | PR #1188(`75eba3fe`): mock 전용, live 금지. 양 arm·모든 client·retry·in-flight 비용을 포함하는 provider/account 강제 상한과 확정 receipt가 미검증이라 실제 실행은 승인 전이다 |
| 논문별 추출 캐시와 실행 재개 | 미착수 | main에 추출 캐시 코드가 없다. 비슷한 것은 요청 간 기억(#60, OPEN)과 continuation 재사용(#797, 닫힘)뿐이다 |
- 남은 일: 추출 캐시·재개 설계(별도 판단 필요), 요인 실험의 실제 실행 승인.

### #621
- 네 갈래가 모두 main에 있다: A(VEP에 AlphaMissense)·C(변이 효과 점검표) PR #626(`6c299527`), B(AlphaGenome 권장 변이 점수 `score_variant`) PR #631(`7c9249a3`)과 다중 염기 REF 창 중앙 보정 PR #1138(`6aac4455`), D(AlphaGenome Atlas 조회, 환경변수 키·필터·출처·cache·timeout) PR #1186(`bebac3e1`)·#1239·#1250(`17db8442`, 영속 credential cache 중단과 ontology 정규화). `labhq/tools/annot_alphagenome.py`에 `score_variant`가 있다.
- 남은 일(gate): 실제 Atlas 조회는 한 번도 하지 않았다(UNVERIFIED). 키를 PI가 정한 위치에 두고 실제 조회 1회를 승인받아야 한다. 결과 문구는 비상업 연구용, 임상 결정 금지를 붙인 채로 있다. #666 hold 범위는 그대로다.

### #546
- 2D 개편 5단계가 병합됐다: #579(토큰 63개와 공통 부품), #582(결정 카드·탭), #585(작업판·메신저·입력), #590(2D 사무실과 캐릭터 1), #599(비교 test와 README 데모 움짤).
- 탈락 시안 보관 위치는 확인했다: `docs/design/2026-10-13/`(`README.md`의 선택 기록과 `mockups`·`characters`·`comparison`·`captures` 폴더)에 A–E 시안과 캐릭터 0–4가 보관돼 있다. 디자인 스킬 장착(#684)은 닫혔다.
- 남은 일: 3D 개편뿐이다. PI 결정(10-13)이 "3D는 나중, 지금은 2D부터"였고 아직 재개 지시가 없다. 재개 조건은 PI의 3D 착수 지시이다.

## 한계
- 닫은 #430의 근거는 이슈 댓글, main의 PR 상태, #578 reference 문서이고, 실제 HPC 실행은 보지 않았다. 처음에는 #578을 "단계 간 전달을 없앤 것"으로 적었다가 봇 리뷰 뒤 "staging으로 푼 것"으로 바로잡았다.
- #612의 WSL, #621의 Atlas, #625의 요인 실험은 실제 실행을 하지 않았다. 코드와 시험 상태만 읽었다.
- #151의 79개 매핑 개별 검수 기록(근거·검수자·날짜)이 있는지는 이번에도 열어 보지 않았다.
