## 2026-10-12 · 파견직 채용물 고정과 외부 전송 선언 (PR #522)

## 결론

파견직 계약서에 원본 commit·license·외부 전송 대상과 labhq가 직접 잰 skill·MCP hash가 남습니다. `external_services`가 있는 파견직은 입력이 restricted 구역에 닿는 단계를 맡지 못합니다. CSO 계획 검증에서 먼저 거부하고, 러너가 엔진을 띄우기 전에 한 번 더 거부합니다. restricted 작업에서 그런 파견직에게 묻는 상담(`colleague:<id>`)도 refs 복사 전에 거부합니다.

## 바뀐 것

- `ContractInfo`·`OFFER_SCHEMA`: `source_commit`(40-hex 검증), `code_license`, `weights_license`, `external_services[{host, what_is_sent}]`. 계약서에는 `skill_sha256`·`mcp_sha256`과 MCP hash를 잰 대상 `mcp_path`(ZIP만 온 경우 그 파일, 리뷰 P2)도 들어가고 재고용 때 같은 대상을 다시 잽니다. 예전 계약서는 기본값으로 읽힙니다.
- hash는 오퍼레터 값을 믿지 않고 `plugin_provenance(..., every_file=True)`로 labhq가 계산합니다. git checkout이면 git 목록, 아니면 모든 파일을 넣고 `.venv`·`__pycache__` 등은 어느 쪽이든 뺍니다. 큰 weight 파일을 생각해 1 MiB 단위로 읽습니다.
- 수습: Paper2Agent `offer_status`가 초안 상태(`mechanical_failure`·`unreviewed`·`unresolved_discrepancies`·`partial`·`blocked`)면 `probation`에 남깁니다. 이유는 `verification.probation_reason`에 적힙니다.
- 재고용: hash를 다시 재서 다르면 `verification.rehire_changed`, `contract.warning` 이벤트(피드 경고), 직원 카드 표시. 재고용 자체는 막지 않습니다.
- restricted 판정: 요청 본문·참고 경로·프로젝트 폴더, 단계 지시, 선행 단계 지시를 `policy.touches`·`overlaps_restricted`로 봅니다. CSO 계획(일반·연구·재계획)은 `PlanAgentError`로 교정을 받고, 단계 task에는 `restricted_inputs` 판정이 실립니다. 러너는 `external_service_refusal`로 엔진 실행 전에 거부합니다. 오류에는 구역 경로를 넣지 않습니다.
- 상담(리뷰 P1): 러너가 묻는 작업의 restricted 판정을 ask token(`source_restricted`, 호출자가 못 지움)에 싣습니다. 게이트웨이는 그 판정이나 요청 자체가 restricted면 외부 전송 파견직 상담을 거부하고, 다른 상담 task에는 `restricted_inputs`를 실어 러너가 다시 확인합니다.
- 수습(리뷰 2차 P1): main에서도 `probation` 파견직은 명단에 남아 CSO가 배정할 수 있었습니다. 문서는 미통과를 "probation 유지 · PI 확인"으로만 적었고, 수습 중 시험 업무를 일부러 허용하는 코드·테스트는 없었습니다. 그래서 의도된 동작이 아닌 누락으로 보고, `active`가 아닌 파견직을 CSO 명단·직접 요청(409)·상담에서 빼고 러너도 거부하게 했습니다. 웹 명단에는 `수습(PI 승인 대기)`로 보입니다. 호스팅 AlphaGenome 예시 계약서(`status: probation`)에는 activate 안내와 `external_services`를 적었고, 러너로 파견직을 돌리던 기존 test 5곳은 계약 상태를 `active`로 명시했습니다(검증 내용은 그대로).
- host(리뷰 2차 P1): `external_services[].host`는 `hostname[:port]`만 받습니다. 오퍼레터가 URL을 주면 hostname만 남기고 인증·경로·query·fragment는 버리며, 노트에도 원문을 옮기지 않습니다.
- 3차 리뷰(P2 3건): 납품된 skill·MCP의 hash를 못 재면 `active`로 올리지 않고 `probation_reason: provenance hash failed`로 남깁니다. 재고용 확인은 기록된 hash가 없는 부분을 건너뛰지 않고 `skill(hash 없음)`으로 표시합니다. `mcp_args`가 `mcp_dir` 밖의 압축 해제된 서버를 실행하면 그 폴더를 `mcp_entry_path`·`mcp_entry_sha256`으로 재고 재고용 때 다시 잽니다. 캐시 checkout의 origin을 읽을 수 없을 때도 pull하지 않고 새로 clone합니다.
- `setup-paper2agent --commit <40-hex>`·`recruit.skill_commit`: 그 commit을 fetch·checkout하고 다르면 기존 설치를 건드리지 않고 멈춥니다. `recruit.skill_source`가 캐시 checkout의 origin과 다르면 pull하지 않고 옆에 새로 clone해 바꿔 끼웁니다(리뷰 P2). 설치한 commit은 `.labhq-source.json`에 남고 채용 때 `verification.paper2agent_commit`이 됩니다.
- 웹: 직원 카드에 commit 7자리·license·외부 전송, 채용 피드 한 줄, 채용 제안 카드에 "채용하면 무엇을 남기는지" 안내. CSO 명단에 외부 전송 호스트 표시. `labhq talent` 출력에 commit·외부 전송.
- `docs/manual.md` 파견직 제도 절, 이벤트 표.

## 실행한 것

- 새 test(`tests/test_contract_provenance.py` 41개 + 리뷰 반영 13개, `tests/web_contract_provenance.cjs` 3개)는 main에서 실패(Python은 수집 단계 ImportError, node는 3개 모두 실패), 변경 뒤 통과. 리뷰 반영 13개는 직전 head(`cf538de`)에서 11개 실패(나머지 2개는 기존 동작 확인용), 수정 뒤 통과. 2차 반영 20개(test_cso 1개 포함)는 직전 head(`33288c7`)에서 15개 실패(나머지 5개는 정상 host·기존 계약서 확인용), 수정 뒤 통과. 3차 반영 5개는 `f4f0b16`에서 모두 실패, 수정 뒤 통과. 캐시 checkout fixture 두 곳(`test_paper2agent_install.py`, 이 파일의 `install`)에는 실제 clone처럼 origin 설정을 넣었습니다(단언 그대로).
- 관련 test(e2e mock·registry·paper2agent 설치·strict schema·cso·plugin hash·workspace boundary): 371 passed, 21 skipped.
- Windows 전체 pytest: 첫 실행 4 failed(plugin hash test가 `_plugin_files`를 인자 하나로 바꿔 끼움, encoding lint, 새 .cjs 미등록). `_folder_files` 분리·`open(path, "rb")`·CI node 목록 등록 뒤 5054 passed, 63 skipped. 리뷰 반영 뒤 5067 passed, 63 skipped. 2차 반영 첫 실행은 러너로 수습 파견직을 돌리던 기존 test 8건 실패, 계약 상태를 active로 명시한 뒤 5087 passed, 63 skipped. 3차 반영 뒤 5092 passed, 63 skipped. basetemp는 종료 뒤 삭제.
- web node test 32개 파일 모두 통과.
- `bash scripts/check_public.sh`, `python scripts/patch_notes.py check` 통과.

## 미해결

- `source_commit`·license·`external_services`는 recruiter의 신고값입니다. labhq는 형식만 검증하고, 선언하지 않은 외부 전송은 막지 못합니다.
- restricted 판정은 경로 문자열 기준입니다. 경로 없이 넘어온 restricted 내용은 잡지 못합니다.
- 헤드헌팅 결과의 license·API 키를 채용 요청으로 넘기는 경로는 만들지 않았습니다. recruiter가 변환 때 저장소에서 직접 확인합니다.
- Paper2Agent 기본값은 아직 고정하지 않습니다(`recruit.skill_commit: null`). PI가 commit을 정하면 그 값을 넣으면 됩니다.

## 근거

- `labhq/models.py`
- `labhq/recruit/paper2agent.py`
- `labhq/recruit/provenance.py`
- `labhq/adapters/claude_code.py`
- `labhq/adapters/mock.py`
- `labhq/registry.py`
- `labhq/runner/daemon.py`
- `labhq/orchestrator/cso.py`
- `labhq/settings.py`
- `labhq/cli.py`
- `labhq/web/ui/strip.js`, `labhq/web/ui/decide.js`, `labhq/web/state.js`, `labhq/web/index.html`
- `tests/test_contract_provenance.py`, `tests/web_contract_provenance.cjs`, `tests/test_e2e_mock.py`, `tests/test_web_3d.py`
- `docs/manual.md`
