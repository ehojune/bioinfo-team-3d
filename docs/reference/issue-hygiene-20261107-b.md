# issue 정리 B (2026-11-07)

lookup-only. #1283 rev3(읽기 전용)의 결과다. 기준은 각 issue의 완료 조건과 마지막 댓글, 그리고 연결된 PR의 상태(`gh pr view`)다. 병합 PR의 CI·리뷰는 병합 조건이므로 다시 열어 보지 않았다. 완료 조건이 충족된 issue는 근거를 달아 닫았고, 나머지는 남은 일과 재개 조건만 댓글로 남겼다.

## 판단 요약

| issue | 판단 | 한 줄 근거 | 남은 일·재개 조건 |
|---|---|---|---|
| #1283 Windows pool 13분 timeout 진단 | **닫음** | 원인·재현 근거 기록, PR #1284(`c7603b1f`)와 #578(`cdd0a570`) 병합 | 없음. helper 하나만 고친 PR의 pool 시간(684 s)은 #1285 |
| #1285 helper가 무거운 시험을 풀에 끌어옴 | 열어 둠 | PR #1289(`628302c0`)로 node 단위 선택, #1292(`d8f73f2e`)로 autouse 보완 | 병목(lock 직렬 231 s)은 그대로. 직렬 시간 예산을 PR 단계에서 알리는 일은 미배정. **helper를 고친 PR이 pool 한계에 다시 닿으면** 정한다 |
| #1300 사이트 프로필 | **닫음** | schema·검사기·질문 흐름·저장소 밖 저장이 PR #1303(`bed5f3e4`), #1309, manual #1304에 병합. 연결은 #1306 | 공개 검사의 flow 표기·anchor 우회는 파싱 기반 검사가 근본 대책(기록만) |
| #1259 #666 보호 backend 설계 독립 검토 | **닫음** | 독립 검토, 정본 v1 대조, PR #1260 최종 diff 검토가 댓글에 있고 #1260 병합(`cacc4758`) | 없음. 실제 보호는 #666 |
| #1079 disposable fixture 계획 검사 | **닫음** | PR #1080(`03f995a0`) 병합. `scripts/boundary_fixture_plan.py`·시험·문서가 main에 있음 | 없음. `plan_valid`는 보호 PASS가 아니다(`protection: UNVERIFIED`) |
| #1047 #666 1단계 launcher | 열어 둠 (gate) | producer PR #1050(`35e02ffe`) 병합, 실제 bwrap 검증은 UNVERIFIED | #666 gate: 관리자 Inspect/EnableFeature 결과 → PI 승인 → disposable VM 준비 → 실제 probe |
| #1048 #666 1단계 broker | 열어 둠 (gate) | PR #1052(`981c1c88`) 병합. 마지막 댓글이 "stays OPEN"을 명시 | 같은 gate. 미연결·기본 off, CLI·스트리밍 호환·hook/MCP 보존·독립 cleanup이 UNVERIFIED |
| #1049 #666 1단계 harness | 열어 둠 (gate) | PR #1051(`8030a1a7`) 병합, 합성 관찰자 | 같은 gate. 실제 보호와 인증된 cleanup UNVERIFIED |
| #1037 #827 증거 수준 검사·공개 마스크 | 열어 둠 (**막힌 것이 풀림**) | 기다리던 producer가 병합됨(#1039 `a619bd9e`, #1040 `e637bc4f`). 초안은 아직 병합 안 됨 | 아래 참고. 코드 일이라 배정하면 진행할 수 있다 |

## #1037: 막힌 것이 풀렸다

- 10-29 마지막 댓글은 "producer PR2 병합 뒤 실제 연결·end-to-end"를 기다린다고 했다. PR2에 해당하는 `labhq_wiki` MCP와 `outputs/wiki_queries.jsonl` 기록(#1040)이 10-29에 병합됐다.
- 초안은 `D:/.labhq/dev/lanes/codex-worker1/wt-827-evidence`(브랜치 `codex/827-evidence-mask`, main보다 628 commit 뒤)에 **커밋 없이** 남아 있다: `labhq/evidence/claims.py`·`report_check.py` 수정, 새 `labhq/evidence/wiki_contract.py`, 새 `tests/test_wiki_evidence.py`(synthetic 28건 통과로 보고됨). main에는 `tests/test_wiki_evidence.py`가 없다.
- 남은 일(issue 완료 조건): main 동기화, 실제 `wiki_queries.jsonl`·증거 검증·게시 경로 연결, 공개 마스크 회귀와 end-to-end, public 검사, Windows inventory 등록, 최종 head CI와 실제 봇 리뷰.
- 새 입력 계약이 있다: `wiki_summary`는 원문 직접 독해·primary 증거가 아니고, origin이 null이면 지지 근거에서 뺀다. JSONL에는 snippet hash만 있어 hash만으로 본문 삭제를 보장하지 못하니, 게시 차단에는 호출별 wiki root와 도구 결과의 원문 공개 여부가 필요하다.
- 주의: 초안은 10-29 기준이라 #1039·#1040 이후 main과 충돌할 수 있다. 작업자가 먼저 `git merge origin/main`으로 범위를 확인한다.

## #666 1단계 세 issue(#1047·#1048·#1049)의 gate

- #666 마지막 댓글(11-02): 기본 off, CLI 미연결 유지, "실제 설치는 관리자 Inspect/EnableFeature 결과 대기", OS 보호 UNVERIFIED.
- 세 issue의 본문은 "실제 보호는 UNVERIFIED로 남고 이후 승인된 시험을 기다린다"고 적었다. 병합된 것은 비모델 합성 시험과 offline 검증까지다.
- 따라서 닫지 않았다. 재개는 위 gate가 열리고 PI가 실제 fixture 설치와 probe를 승인할 때다. 그 전에는 새 일이 없다.

## 한계

- 병합 PR의 CI 결과와 리뷰는 병합 사실로 갈음했고 run을 다시 보지 않았다.
- #1037 초안의 28건 통과는 10-29 댓글의 보고이며 이 정리에서 다시 돌리지 않았다.
- #666 gate의 현재 상태는 11-02 댓글 기준이다. 그 뒤 관리자 결과가 있었다면 반영하지 못했다.
