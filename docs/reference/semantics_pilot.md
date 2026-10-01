측정 코드 `898352f`(diamond 출력 줄은 이 보고 커밋) · model `labhq.provenance@1` `8fd9860f5f480831ce2794ee585b513e1da8ad5f7c1bc8e4c49d6c06d0548159` · fixture `24dce1ed83a06b78d000606111a785e6eb388e0c39c262ecad4442b1700cafeb` · expected `e8f094595f3e4c19056df37ac2ee54425c8d5d20499e312e6f59671e05671c02`

# 출처·재사용 의미 모델 pilot 측정 (#127)

조회용 기록이다. 합성 fixture 결과이며 실제 요청에서의 효과가 아니다. 재현: `python scripts/semantics_pilot.py`(`--json`, `--quick`).

## 판정

**중단 기준 충족. 그러나 PI 결정(2026-10-01)으로 접지 않고 opt-in으로 유지한다 — 그림자 모드로 실데이터를 모아 #121에서 재평가한다.** 기준선 A가 같은 의미와 정확도를 냈다(세 상태 모두 17/17, 잘못된 동일시 0, 소비자 불일치 0). B가 앞선 것은 성능뿐인데, 그 차이는 모델이 아니라 A 구현 방식(N+1 SQL)에서 나왔다.

| 채택 조건(issue #127) | 결과 | 충족 |
|---|---|---|
| B 17/17·잘못된 동일시 0·소비자 불일치 0 | 17/17·0·0 (base·change1·change2) | 예 |
| A보다 독립 결함 유형 2건 이상 적음 | 17문항 안: A 0·B 0. 밖의 반례: B 3유형(gen·resume·indep), A 2유형(scope·traverse), 모두 고침 | 아니오 |
| 또는 같은 정확도로 두 변경 모두 시간 30% 이상 짧음 | 변경 1: B 3분·A 1분, 변경 2: B 1분·A 1분 | 아니오 |
| 최초 매핑 ≤2시간·변경 ≤30분 | B 11분, 변경 최대 3분 | 예 |
| cold·warm p95 B/A ≤1.1 | 1×: 0.56·0.07, 50×: 0.05·0.005 | 예(근거 약함, 아래) |
| 추가 LLM 호출 0 | socket·subprocess·os 프로세스 시작 함수 차단 속 실행, import allowlist(script 포함) | 예 |

중단 조건 "A가 같은 의미·정확도를 확보"에 해당한다. 기대 답은 고정 전 보정 1회 뒤 바꾸지 않았다.

- 유지하는 꼴: 설정 키 0, 실행 경로(cli·gateway·runner·orchestrator) import 0, `settings.py` diff 0, CSO prompt diff 0. A(기준선)는 `tests/`에 남겨 공정 비교 자료로 계속 쓴다.
- 다음: 그림자 모드(실 state DB를 읽기 전용으로 읽고 답만 기록)와 팔란티어식 운영 객체 뷰는 다음 PR에서 다룬다. 접는 순서(#143)는 접기로 정할 때 새로 연다.

## 정답과 결함

| | 상태 | 정답 | forbidden 위반 | 소비자 불일치 | 독립 결함(원인) |
|---|---|---|---|---|---|
| B | base → change1 → change2 | 17 → 17 → 17 | 0 | 0 | 0 |
| A | base → change1 → change2 | 17 → 17 → 17 | 0 | 0 | 0 |

17문항 밖 반례는 모두 고쳤고, 수정 전 실패하던 회귀 test가 있다.

| 유형 | 모델 | 반례 | 고친 곳 |
|---|---|---|---|
| gen | B | 한 출력을 두 철자로 보고 → reported_output 두 번 | `fb9c061` |
| resume | B | 맞는 세션 없음 → 빈 후보 목록 / 앞 run 시각 unknown → TypeError | `fb9c061` / #137 |
| indep | B | 같은 claim revision을 두 결과가 보고 → independent_groups가 마지막 결과만 | #139 |
| scope | A | 같은 반례 → claim 표 PRIMARY KEY 충돌 | #138 |
| traverse | A | 재귀 CTE가 경로를 열거 → diamond DAG에서 지수 시간(16층 1.83초) | #140 |

- B의 앞 두 결함은 A를 쓰면서, 나머지는 구현 뒤 독립 리뷰(Codex)와 검증에서 찾았다. 같은 작업자가 B를 먼저 쓴 순서 효과가 있다.
- 고친 뒤 A와 B는 반례 넷(resume 시각 unknown, claim 반복 보고, 20층 diamond, cycle·깊이 70)에서 같은 답을 낸다. A의 노드 단위 순회는 고정 fixture 계보 루트 35개 전부에서 고치기 전과 출력이 같다.
- 공유 부분 두 건도 고쳤다(모델 비교에는 안 센다): reader의 immutable 읽기가 열기 전후에 시작한 writer의 commit을 놓치던 것(#141), LLM 0 차단이 `os.spawn*`·`os.exec*`·`os.posix_spawn*`을 막지 않고 import 검사가 script를 안 보던 것(#142).
- pilot 뒤 후속(17문항·판정 변화 없음): cycle 경고는 강연결 요소마다 한 건(#154), 계보 순회는 노드별 최소 깊이(#157), 맞는 세션이 있는데 시각을 모르는 resume은 이유 `start_unknown`(#155). #155는 모델 개정이라 hash가 `9ed9aa1eecdb41e9dc062e1184cdaacd7d94b2f7d4c335354eeb99ea7b561b42`로 바뀌었다. 위 측정은 이전 hash 기준이다.
- 원인 단위 집계는 변형 test로 확인했다. A8.generated_by 하나를 틀리면 질의 다섯 개가 틀려도 결함은 `gen` 1건이다.

## 시간(Pilot-Minutes, 에이전트 작업 시간)

| 단계 | B | A | 공통 |
|---|---|---|---|
| fixture·기대 답 | | | 27 |
| 보정 1·hash 고정 | | | 12 · 2 |
| 최초 구현 | 11(공유 reader·채점 포함) | 4 | |
| 측정·격리 test | | | 8 |
| 구현 뒤 리뷰·검증 | | | 12 |
| 결함 수정 | 2 | 0 | |
| 변경 1(B→A) | 3 | 1 | |
| 변경 2(A→B) | 1 | 1 | |
| 후속 결함 수정(#137–#142) | 약 2(#137·#139) | 약 3(#138·#140) | 약 4(#141·#142) |

- 두 변경 모두 B는 YAML만 고쳤다. 코드 분기(`judge_reuse`의 scope=run, `judge_data_type`의 declared)는 변경을 알고 B를 처음 쓸 때 이미 넣었다. A도 output_types 칸을 미리 두었다. 그래서 변경 시간은 B 쪽으로 기울어 있는데도 B가 짧지 않았다.
- 변경 내용을 구현 전에 알고 있었으므로 이 시간 지표는 변경 비용을 재지 못한다. 다시 잰다면 변경을 구현 뒤에 공개해야 한다.
- 후속 수정 시간은 커밋 간격으로 셌고 issue를 읽은 시간은 뺐다. 결함 수정은 A와 B 모두 YAML이 아니라 코드를 고쳤다.

| 구현 파일 변경 | hunk | 줄 |
|---|---|---|
| 변경 1 B / A | 2 / 3 | +4 −4 / +4 −2 |
| 변경 2 A / B | 4 / 3 | +18 −3 / +7 −4 |

## 코드 크기

| | 의미 처리 | 출력 변환 | 공유 reader | YAML |
|---|---|---|---|---|
| B | 741줄 | 24줄 | 180줄 | 105줄 |
| A | 405줄 | 115줄 | 0 | 0 |

AST로 함수 단위로 셌다. A의 `run_row`·`artifact_row`는 unknown 이유 판정도 함께 하므로 출력 변환 115줄은 과대 계산이다. 유지할 줄은 B가 A의 두 배쯤이다. 후속 수정으로 B +7·reader +32, A +12줄이 늘었다.

## 성능

| 크기 | cold p95 A / B | warm p95 합 A / B | B/A cold · warm | 질의별 B/A 최대 | tracemalloc peak A / B |
|---|---|---|---|---|---|
| 1× | 12.54 / 7.00 ms | 8.07 / 0.58 ms | 0.56 · 0.07 | 0.28 (q08) | 0.4 / 0.5 MB |
| 50× | 5457 / 291 ms | 4860 / 22 ms | 0.05 · 0.005 | 0.41 (q08) | 17.1 / 18.3 MB |

- 1×: 질의마다 warm-up 20회 뒤 200회, A·B를 번갈아 5 batch, batch p95의 중앙값. cold는 20회. 50×는 warm-up 3·20회·5 batch, cold 3회다.
- A의 `find_reusable`은 artifact마다 SQL을 여러 번 부른다(N+1). 50×에서 q03·q04가 1.6초 걸린 것은 그 탓이다. 모델의 효과로 세지 않는다. 노드 단위 순회로 바꾼 뒤 50×의 계보 질의는 대체로 빨라졌다(q09 17→13 ms, q10 17→10 ms).
- cycle(3 run)과 깊이 70 사슬은 A·B 모두 끝났고 각각 `cycle`·`depth_limit` caution을 냈다. 20층 diamond(경로 2^19개)는 A 1.9 ms·B 0.2 ms, 둘 다 edge 118개다.

## 격리 확인

| 항목 | 방법 |
|---|---|
| 설정 | `labhq/settings.py` diff 0, ResearchSettings에 semantics 키 없음 |
| 실행 경로 | fresh process로 cli·gateway·runner·orchestrator·labhq.research import 뒤 semantics 미로드, 기존 모듈 semantics import 0(AST) |
| CSO | pilot 실행 전후 CSO prompt 상수·연구 schema hash 동일, adapter 미로드 |
| LLM 0 | socket·subprocess·os.system·asyncio subprocess, 이 플랫폼의 `os.spawn*`·`os.exec*`·`os.posix_spawn*`·popen·fork를 막은 채 실행. AST allowlist는 semantics.py·기준선·script를 보고, script의 os·socket·subprocess import는 차단 함수 안에서만, 동적 import는 허용 이름만 |
| 쓰기 | 읽고 답한 뒤 fixture와 임시 사본의 파일 목록·bytes 동일. rollback journal DB는 `mode=ro`, -wal 없는 WAL DB만 immutable로 열고 읽은 뒤 writer(-wal 생성·본 파일 변경)를 다시 확인해 있으면 다시 읽는다 |
| 공개 | pilot 파일(미추적 포함)에 드라이브·홈 경로·이메일 없음, `check_public.sh` 통과 |

## 한계

- 합성 기록 16행·manifest 8개뿐이다. A·B가 같게 나온 것도 이 범위의 결과다. 실데이터 판단은 그림자 모드 기록으로 #121에서 한다.
- 같은 작업자(Claude Opus 5.5)가 B, A 순서로 썼다. A는 B에서 정한 규칙과 출력 꼴을 그대로 받았다.
- 기대 답 검토는 독립 검토 1회(구현 전)뿐이다. 구현 뒤 독립 리뷰는 2회(Codex, 후속 수정 뒤 1회 포함)이고, 17문항 밖 반례 탐색은 체계적이지 않다.
- immutable 읽기의 writer 재확인은 크기·mtime과 -wal로 본다. 같은 mtime 단위 안에서 크기를 바꾸지 않고 checkpoint까지 끝내는 writer는 놓칠 수 있다.
