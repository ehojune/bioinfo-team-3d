## 단계
#1037 rev13: 두 번째 단위 — 보고서·업로드·라운드·pipeline 기여·CP2. 공개 보호는 아직 **UNVERIFIED**.

## 외부에 쓰는 지점(목록)
| 지점 | 파일 | 처리 |
|---|---|---|
| 단계별 진행 게시 | `result_storage.py` `progress_step` | 첫 단위에서 막음 |
| 프로젝트 issue 생성 | `integrations/github.py` `handle` | 막음 |
| issue 댓글(plan·review·recruit·완료·실패) | `_comment_once`, `handle` | 막음 |
| 보고서·부록 파일 | `handle` | 막음 |
| issue 닫기 | `handle` | 최상단 확인에서 막음 |
| pipeline 기여(승인 카드, branch, 파일, PR) | `_handle_pipeline` | 카드 전과 각 쓰기 직전에 막고 `rejected` |
| 라운드 issue 생성·수정 | `rounds.py` `publish`, `_may_post` | 막음 |
| 연구 결과 올리기(문서·그림·PDF) | `result_storage.py` `upload` | 시작과 파일마다 막음 |
| 저장소·branch 생성(내용 없음) | `result_storage.py`, `github.py` | 내용이 없어 막지 않음 |
| CP2 근거 카드 | `orchestrator/cso.py` | 막지 않고 경고 한 줄(PI의 검토) |

## 한 일
- 위 지점을 쓰기 직전에 `wiki_exposure.publication_blocker`로 다시 확인한다(확인과 전송 사이에 await 없음). 막히면 한 번 로그하고 다시 시도하지 않는다.
- CP2: `wiki_exposure.cp2_warning` 한 줄을 CP2 카드의 `warnings`와 보고서 한계에 더한다.
- 시험(`tests/test_wiki_publish.py`): reporter(이력 없음·possible·unknown·빈 값·대소문자 오류 모두 쓰기 0건, recorded none은 issue를 쓴다), 대기 중 노출이 올라가면 다음 쓰기 중단, pipeline 거절, 라운드, 최종 업로드, CP2 경고.
- manual 12장 한영, 참고 문서 갱신.

## 확인
- Windows: `tests/test_wiki_publish.py` 포함 wiki·진행 게시·CP2 시험 133 passed.

## 추가(rev14)
- 기록 없는 요청은 `created_at`이 labhq_wiki 출시(2026-10-29 21:01:56Z) 이전일 때만 none. 그 뒤 업그레이드 전에 만든 요청은 막히므로 새로 요청해야 한다(manual 12장).

## 막힌 것
- (해결) 기존 시험 fixture 12개 파일은 rev14에서 none을 기록했다.

## 하지 않은 것
- 재시작·구형 runner 통합 시험, 이어 묻기 요청의 노출 상속, 증거 수준 검사, 독립 출처 인증, wiki 본문 마스크. 결과 저장소 선택 카드의 표시.
