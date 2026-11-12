# #1388 r2 — 독립 경로 삭제안

## 결론

#1388 r2 독립 경로 구현은 [PR #1399](https://github.com/ehojune/bioinfo-team-3d/pull/1399)의 draft/hold로 보존했습니다. 통합·병합하지 않습니다. 실제 checkout의 통합 실패는 다음 claim이 필요합니다.

## 변경

- live 그림자·객체 뷰·액션·A/B·새 장치 보고서와 CLI 연결을 소유 경로에서 제거했습니다.
- core input-fit 6시험을 이관하고 fresh CP1 import 차단, 어휘·EDAM·점검표·pack, typed-output, continuation round binding, 오류 로그·공개 redaction assertion을 보존했습니다.
- legacy `semantics`·`research.semantic_report`만 비활성 입력으로 받습니다. 직접 validation·생성자·파일 load에서 다른 unknown key 거부를 유지합니다.
- 오프라인 #136 모델·pilot을 보존했습니다. README·매뉴얼 19파일: 309,559자 → 281,974자.

## 확인

| 확인 | 결과 |
|---|---|
| 실제 checkout 관련 회귀 | 742 passed · 25 failed · 2 skipped, 184.86초 |
| 실패 25건 | 남은 server terminal·portfolio fallback 연결. import 오류·terminal 지연·bundle pending assertion |
| 격리 진단 | 임시 사본의 두 연결만 정리한 뒤 위 실패 25건 + mock 종단 + mock bench = 27 passed, 161.04초 |
| 전체 수집 | 15,642 tests collected |
| Node 웹 시험 | 8 passed |
| 정적 추적 | Python 3.10 AST 통과. 삭제 모듈 참조는 미소유 `server.py`·`portfolio.py`에만 남음 |
| 미소유 파일 보존 | server·portfolio·portfolio edition 시험·Windows inventory·daemon을 기준 `a4b5aaa2`와 대조, 동일 |
| 삭제 줄 수 | 제품 5,406 삭제·46 추가, 시험 5,985 삭제·222 추가, 도구 296 삭제. rename 감지 없이 실측 |

첫 넓은 실행은 mock bench 완료가 관측되지 않아 종료했습니다. bench를 뺀 명령으로 위 checkout 결과를 얻었고, 격리 진단의 mock bench는 통과했습니다. 격리 PASS는 실제 가지의 통합 PASS가 아닙니다. 실제 모델·PI 인스턴스·HPC 실행은 없었습니다.

## hold와 다음 claim

초기 head CI에서 Windows inventory 미등록, 배정된 status 파일명의 HHMM 누락, clean-install의 요청 묶음 누락을 확인했습니다. patch-note 행은 후속 기록 커밋에서 보완합니다. CI를 재실행하거나 검사 조건을 낮추지 않았습니다.

다음 claim에 필요한 경로:

```text
labhq/gateway/server.py
labhq/portfolio.py
tests/test_portfolio_edition.py
config/ci/windows-boundary-v1.json
tests/test_model_refusal_682.py
docs/manual.en.md
docs/manual/04-install.md
docs/manual/04-install.en.md
docs/manual/17-known-limits.md
docs/manual/17-known-limits.en.md
docs/status/2026-11-12-1048-codex-worker2-prune-1388.md
```

기존 status 파일은 보존했습니다. 새 이름으로 옮길 claim이 필요합니다. server·portfolio는 실제 checkout에서 편집하지 않았고, 과거 report 사본 판독·공개 안전성은 통합 때 다시 확인해야 합니다.

최종 전체 시험·최종 head CI·같은 head 실제 리뷰 P1=0·실제 가지 mock 종단·import 추적과 #1375 통합 trial 비교를 마칠 때까지 hold를 유지합니다. #1388은 열어 둡니다.

[범위·이전 조사](docs/reference/prune-semantics-1388.md)
