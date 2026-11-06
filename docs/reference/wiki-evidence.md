# wiki 노출 이력과 공개 게시 차단 (#1037)

lookup-only. 동작 설명은 [manual 12장](../manual/12-github-reports.md), 증거 계약은 #827 댓글에 있다. 증거 수준 검사(`wiki_summary`를 원문 직접 독해로 승격하지 못하게 하는 규칙)의 초안은 이 PR에 넣지 않았고 브랜치 `codex-worker1/1037-lane`에 있다(스키마 변경이 엔진 결과 계약의 hash와 prompt 규칙에 닿아 따로 다룬다). 공개 보호는 아직 **UNVERIFIED**다.

## 정한 것

| 항목 | 값 |
|---|---|
| 기록 | 요청 기록의 `wiki_exposure` (`labhq/evidence/wiki_exposure.py`) |
| `none` | 이력을 적는 게이트웨이가 만든 요청이고, wiki 도구를 줄 수 있는 직원에게 task를 보낸 적이 없다 |
| `possible` | 그런 직원에게 task를 보냈다. 다시 `none`이 되지 않는다 |
| 없음·그 밖의 값 | **unknown**. 이력이 없거나 읽을 수 없다. 단 `created_at`이 숫자이고 `labhq_wiki` 출시(PR #1040 병합, 2026-10-29T21:01:56Z) 이전이면 `none`으로 본다(그때는 도구가 없었다). 출시 이후거나 `created_at`이 없으면 unknown |
| 공개 게시 | `none`일 때만 허용한다. `possible`·unknown은 막는다 |

## 어떻게 묶었나

- 게이트웨이 `dispatch`가 task를 보내기 직전에(`_bind_wiki_exposure`) 그 runner가 보낸 roster의 `wiki_tools`로 task `meta["wiki_tools"]`를 정하고, wiki가 가능하거나 알 수 없으면 요청을 `possible`로 올린다. 저장은 전달보다 먼저다.
- roster에 `wiki_tools` 키가 없거나(옛 runner), 그 직원이 roster에 없으면 알 수 없는 것이므로 `possible`로 올리고 task에는 허가를 주지 않는다.
- runner는 `meta["wiki_tools"]`가 참인 dispatch에만 `labhq_wiki`를 붙인다(`_mcp_servers(..., wiki_bound=True)`). 전에 보낸 roster가 나중의 도구 구성을 허가하지 못한다.
- 이력이 없고 출시 이후인 요청은 dispatch가 와도 기록하지 않는다(unknown 그대로). 출시 이전 요청은 `none`으로 보므로 wiki 직원에게 task가 가면 `possible`이 된다.

## 이번 단계가 막는 것과 아직 아닌 것

| 경로 | 상태 |
|---|---|
| 단계별 GitHub 진행 게시(`ResultStorage.progress_step`) | 막음 |
| `ProjectReporter`: 프로젝트 issue·댓글·보고서·부록·종료, pipeline 기여(승인 카드 전, branch·파일·PR 직전) | 막음(쓰기 직전 재확인) |
| `RoundRecorder`: 라운드 issue 쓰기 | 막음(쓰기 직전 재확인) |
| `ResultStorage.upload`: 연구 결과 올리기(문서·그림·PDF 파일마다) | 막음(쓰기 직전 재확인) |
| CP2 근거 카드 | 막지 않고 경고 한 줄(PI의 검토이지 게시가 아님) |
| 진행 게시 안의 경쟁(게시 도중 노출이 올라감) | 막음: 파일마다 쓰기 직전에 다시 확인 |
| 재시작·구형 runner의 통합 시험, 이어 묻기 요청의 노출 상속 | **아직 아님** |
| 증거 수준 검사(`wiki_summary` 승격 금지)와 결과 계약 prompt 규칙, 독립 출처 인증, wiki 본문·경로 마스크 | 후속 |

## 한계

- 이 기록은 "wiki 도구를 쓸 수 있었다"이지 "복사됐다"가 아니다. `possible`이면 복사 여부와 무관하게 막는다.
- 결과 저장소 선택 카드는 막힌 요청에도 올리기를 제안하고, 올리려 할 때 거절한다(카드 표시는 아직 바꾸지 않았다).
- 이어 묻기·follow-up 요청이 원래 요청의 노출을 이어받는 규칙은 아직 없다. 새 요청은 `none`으로 시작한다.
