# #1283 — #578 Windows pool 13분 timeout 진단

결론: hang·lock 고장·제품 회귀가 아니라 **Windows 시험 선택이 커져 풀이 한계를 넘은 것**입니다. #578이 공용 시험 helper `tests/semantics_shadow_lab.py`를 고쳐서, 그 helper를 쓰는 무거운 시험이 Windows 풀에 들어옵니다.

## 증거

| 질문 | 근거 | 값 |
|---|---|---|
| 느린 것이 #578만인가 | 최근 워크플로 125회의 pool step(성공 58) | 보통 PR 중앙값 413 s·최대 572 s. #578 head 3개는 759·792·793 s (한계 780 s) |
| 멈춘 시험이 있나 | 실패 run 42021211558 아티팩트(`parallel/lock-state-*.json`, `diagnostics-*.json`) | 한계 직전에도 lock이 정상으로 넘어가고 있음(gw0 `acquired`는 775.8 s에 막 잡음). 진행률 97%. 멈춘 node 없음 |
| 왜 느린가 | 같은 아티팩트의 `host_resource_wait_s` | #578 실패: gw1 266 s·gw2 246 s 대기. 통과한 main run(`c673f574`, 430 s)은 4개 워커 모두 0 s |
| 무엇이 병목인가 | `diagnostics` slowest | `test_semantics_shadow_remove.py`의 두 node가 hosted에서 79.5 s·82.0 s. host-resource lock 안에서 직렬로 돌고, 다른 워커가 최대 223 s 기다림 |
| 선택 크기 | `collection-gw0.json` | 선택 node #578 6297 / main 5364 (+934), 제외 파일 277 / 299 |
| 왜 더 선택됐나 | 같은 파일들에 `scripts/ci_selection.include_windows`를 변경 목록 45개로 다시 실행 | 추가 31개 파일 중 22개가 "directly imported changed OS test helper", 7개 "direct OS/runtime API", 1개 mixed, 1개 essential |
| 어느 helper인가 | #578 diff | `tests/semantics_shadow_lab.py` 4줄(실행 위치 게이트 응답). 이 helper를 쓰는 8개 파일(`test_semantics_shadow_remove`·`_worker`·`_actions_shadow` 포함)이 풀에 들어옴 |
| 제품 회귀인가 | 로컬 Windows 4-worker 전체 시험 | #578 head 405 s, main 451 s. node 시간 합 1419 s 대 1485 s. host-resource lock 직렬 시간 249 s 대 267 s. 로컬에서는 느려지지 않음 |
| 전에도 이 helper를 고친 PR이 있었나 | #1126(533 s)·#1248(522 s) | 한계 안이었으나 중앙값보다 약 110 s 길었음 |

## 해석

- 풀 시간은 기본 선택(약 420 s)에 선택된 host-resource 시험의 직렬 시간이 더해져 정해집니다. 로컬 기준 shadow 시험 세 파일의 lock 직렬 시간이 약 160 s이고 hosted는 더 느립니다(`remove` node 하나가 로컬 약 50 s, hosted 약 80 s).
- 이 helper를 고치는 PR은 이미 +110 s를 지고, #578은 그 위에 자기 시험(`test_e2e_mock` 등 7개 파일)까지 더해 780 s를 넘습니다. 같은 PR이 다시 돌아도 같은 결과입니다(759·792·793 s로 세 번 일관).
- 마지막 출력만으로 hang과 느림을 구별할 수 없던 것은 맞습니다. 이번 아티팩트의 lock 상태가 둘을 갈랐습니다.

## 수정 (이 PR)

1. **helper 변경을 #578에서 분리해 작은 PR로 먼저 병합합니다.** 변경은 `tests/semantics_shadow_lab.py` 한 파일, 응답을 `OPTIONS[1]`(#578에만 있는 모듈) 대신 상수 `"1: b"`로 적으면 #578 없이도 동작합니다(main에는 그 게이트가 없어 영향 없음). 이 PR의 풀은 #1126·#1248처럼 약 530 s 예상입니다.
2. **#578은 main을 합친 뒤** helper를 건드리지 않습니다. 시뮬레이션(변경 목록에서 helper를 뺀 선택)에서 shadow 8개 파일이 빠지고 나머지 23개 파일(로컬 116 node-초)만 남습니다.
3. 한계 시간 증액·시험 제외는 하지 않습니다(CI 계약 유지).
4. 후속 후보(별도 issue): `include_windows`의 "직접 import한 변경 helper" 규칙이 helper 하나로 무거운 lock 시험 수십 개를 끌어오므로, 풀 시간 예산을 선택 크기로 미리 알려 주는 검사.

## 확인한 것 (이 PR)

- 상수로 바꾼 helper로 shadow 시험 8개 파일(`test_semantics_ab`·`_actions_shadow`·`_input_fit`·`_shadow_breaker`·`_shadow_hash`·`_shadow_provenance`·`_shadow_remove`·`_shadow_worker`)을 **main에서** 돌려 208 passed, 1 skipped.
- 같은 helper를 #578 head(`e3366036`)에 얹어 같은 8개 파일을 돌려 208 passed, 1 skipped. 게이트가 실제로 뜨는 쪽에서도 `"1: b"`로 통과합니다.

## 검증하지 못한 것

- 분리 후 #578의 실제 pool 시간은 예측일 뿐입니다(미실행). 새 head CI로만 확인됩니다.
- hosted에서 shadow 시험이 로컬보다 몇 배 느린지는 #578 아티팩트 한 개로만 봤습니다(약 1.6배, 80 s 대 50 s).
- `test_semantics_*`가 hosted main에서 선택되지 않아 같은 node의 hosted 기준선이 없습니다.

근거 파일(작업 폴더): `D:\.labhq\dev\tmp\claude-worker1\` — `art578\`(실패 run 아티팩트), `artmain\`(main run), `pool.tsv`, `p1283-578.log`·`p1283-main.log`(로컬 시간), `reasons1283.py`·`sim1283.py`.
