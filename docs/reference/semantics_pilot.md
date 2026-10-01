commit `78ad491` · model `labhq.provenance@1` `8fd9860f5f480831ce2794ee585b513e1da8ad5f7c1bc8e4c49d6c06d0548159` · fixture `24dce1ed83a06b78d000606111a785e6eb388e0c39c262ecad4442b1700cafeb` · expected `e8f094595f3e4c19056df37ac2ee54425c8d5d20499e312e6f59671e05671c02`

# 출처·재사용 의미 모델 pilot 측정 (#127)

조회용 기록이다. 합성 fixture 결과이며 실제 요청에서의 효과가 아니다. 재현: `python scripts/semantics_pilot.py`(`--json`, `--quick`).

## 판정

**중단 기준 충족 — 접기를 권고한다.** 기준선 A가 같은 의미와 정확도를 냈다(세 상태 모두 17/17, 잘못된 동일시 0, 소비자 불일치 0). B가 앞선 것은 성능뿐인데, 그 차이는 모델이 아니라 A 구현 방식(N+1 SQL)에서 나왔다.

| 채택 조건(issue #127) | 결과 | 충족 |
|---|---|---|
| B 17/17·잘못된 동일시 0·소비자 불일치 0 | 17/17·0·0 (base·change1·change2) | 예 |
| A보다 독립 결함 유형 2건 이상 적음 | 17문항 안: A 0·B 0. 밖의 반례: B 2(gen·resume), A 0 | 아니오 |
| 또는 같은 정확도로 두 변경 모두 시간 30% 이상 짧음 | 변경 1: B 3분·A 1분, 변경 2: B 1분·A 1분 | 아니오 |
| 최초 매핑 ≤2시간·변경 ≤30분 | B 11분, 변경 최대 3분 | 예 |
| cold·warm p95 B/A ≤1.1 | 1×: 0.64·0.07, 50×: 0.11·0.005 | 예(근거 약함, 아래) |
| 추가 LLM 호출 0 | socket·subprocess 차단 속 실행, import allowlist | 예 |

중단 조건 "A가 같은 의미·정확도를 확보"에 해당한다. 기대 답은 고정 전 보정 1회 뒤 바꾸지 않았다. 접을 때 할 일은 issue #127대로다: `semantics.py`·YAML·script를 지우고 fixture·`expected.yaml`·제약 test를 #60 회귀 시험(없으면 독립 test)으로 남긴다. 이 PR에서는 지우지 않았다.

## 정답과 결함

| | 상태 | 정답 | forbidden 위반 | 소비자 불일치 | 독립 결함(원인) |
|---|---|---|---|---|---|
| B | base → change1 → change2 | 17 → 17 → 17 | 0 | 0 | 0 |
| A | base → change1 → change2 | 17 → 17 → 17 | 0 | 0 | 0 |

- 17문항 밖 반례 2개(맞는 세션이 없는 resume, 한 출력을 두 철자로 보고)에서 B만 A와 달랐다. B가 reported_output을 두 번 싣고 빈 후보 목록을 냈다. `fb9c061`에서 고쳤다. A는 relation 표의 UNIQUE와 조건부 칸으로 처음부터 맞았다.
- 두 결함은 A를 쓰면서 찾았다. 같은 작업자가 B를 먼저 쓴 순서 효과다.
- 원인 단위 집계는 변형 test로 확인했다. A8.generated_by 하나를 틀리면 질의 다섯 개가 틀려도 결함은 `gen` 1건이다.

## 시간(Pilot-Minutes, 에이전트 작업 시간)

| 단계 | B | A | 공통 |
|---|---|---|---|
| fixture·기대 답 | | | 27 |
| 보정 1·hash 고정 | | | 12 · 2 |
| 최초 구현 | 11(공유 reader·채점 포함) | 4 | |
| 측정·격리 test | | | 8 |
| 결함 수정 | 2 | 0 | |
| 변경 1(B→A) | 3 | 1 | |
| 변경 2(A→B) | 1 | 1 | |

- 두 변경 모두 B는 YAML만 고쳤다. 코드 분기(`judge_reuse`의 scope=run, `judge_data_type`의 declared)는 변경을 알고 B를 처음 쓸 때 이미 넣었다. A도 output_types 칸을 미리 두었다. 그래서 변경 시간은 B 쪽으로 기울어 있는데도 B가 짧지 않았다.
- 변경 내용을 구현 전에 알고 있었으므로 이 시간 지표는 변경 비용을 재지 못한다. 다시 잰다면 변경을 구현 뒤에 공개해야 한다.

| 구현 파일 변경 | hunk | 줄 |
|---|---|---|
| 변경 1 B / A | 2 / 3 | +4 −4 / +4 −2 |
| 변경 2 A / B | 4 / 3 | +18 −3 / +7 −4 |

## 코드 크기

| | 의미 처리 | 출력 변환 | 공유 reader | YAML |
|---|---|---|---|---|
| B | 734줄 | 24줄 | 148줄 | 105줄 |
| A | 393줄 | 115줄 | 0 | 0 |

AST로 함수 단위로 셌다. A의 `run_row`·`artifact_row`는 unknown 이유 판정도 함께 하므로 출력 변환 115줄은 과대 계산이다. 유지할 줄은 B가 A의 두 배쯤이다.

## 성능

| 크기 | cold p95 A / B | warm p95 합 A / B | B/A cold · warm | 질의별 B/A 최대 | tracemalloc peak A / B |
|---|---|---|---|---|---|
| 1× | 15.40 / 9.84 ms | 7.58 / 0.50 ms | 0.64 · 0.07 | 0.24 (q08) | 0.4 / 0.5 MB |
| 50× | 4120 / 440 ms | 5105 / 23 ms | 0.11 · 0.005 | 0.27 (q08) | 17.2 / 18.2 MB |

- 1×: 질의마다 warm-up 20회 뒤 200회, A·B를 번갈아 5 batch, batch p95의 중앙값. cold는 20회. 50×는 warm-up 3·20회·5 batch, cold 3회다.
- A의 `find_reusable`은 artifact마다 SQL을 여러 번 부른다(N+1). 50×에서 q03·q04가 1.7초 걸린 것은 그 탓이다. 모델의 효과로 세지 않는다.
- cycle(3 run)과 깊이 70 사슬은 A·B 모두 끝났고 각각 `cycle`·`depth_limit` caution을 냈다.

## 격리 확인

| 항목 | 방법 |
|---|---|
| 설정 | `labhq/settings.py` diff 0, ResearchSettings에 semantics 키 없음 |
| 실행 경로 | fresh process로 cli·gateway·runner·orchestrator·labhq.research import 뒤 semantics 미로드, 기존 모듈 semantics import 0(AST) |
| CSO | pilot 실행 전후 CSO prompt 상수·연구 schema hash 동일, adapter 미로드 |
| 쓰기 | 읽고 답한 뒤 fixture와 임시 사본의 파일 목록·bytes 동일. WAL 파일이 없는 DB는 immutable로 열어 -wal·-shm을 만들지 않는다 |
| 공개 | pilot 파일(미추적 포함)에 드라이브·홈 경로·이메일 없음, `check_public.sh` 통과 |

## 한계

- 합성 기록 16행·manifest 8개뿐이다. A·B가 같게 나온 것도 이 범위의 결과다.
- 같은 작업자(Claude Opus 5.5)가 B, A 순서로 썼다. A는 B에서 정한 규칙과 출력 꼴을 그대로 받았다.
- 기대 답 검토는 독립 검토 1회(구현 전)뿐이다. 구현 뒤 독립 리뷰는 이 기록에 들어 있지 않다.
