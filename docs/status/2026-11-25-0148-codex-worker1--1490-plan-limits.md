# 2026-11-25 codex-worker1 · #1490 r7 · 간소화 연구 계획

Refs #1490

## 결론

`research.compact_workflow`를 켜면 새 연구 요청의 갈래 없는 계획은 설정 상한 안에서 최대 3단계로 짭니다. 옵션은 요청을 만들 때 저장합니다. 기본값은 꺼짐입니다.

## 바뀐 것

- 계획 안내, 재계획, 검증에 같은 상한을 적용합니다. 갈래 계획은 기존 상한을 따릅니다.
- 갈래 없는 계획의 각 단계에 QC 지시를 붙입니다. 맡은 작업에 분석·QC·해석이 포함되면 승인된 범위에서 공개 시료 기록 대조, 대조군과 기대 방향 기록, 문헌 값 비교를 수행합니다.
- 연구 매뉴얼 한영판에 설정 방법, 적용 대상, 재개 동작과 분석 지시를 적었습니다.

## 확인

- CP1·CP2·CP4·분기·연구 재개·계획 계약 시험 277개가 통과했습니다. `evidence`·`validation`과 `report`의 QC 누락을 재현했고, 수정 뒤 `phase` 6종 모두에서 옵션에 따라 지시가 전달되는지 확인했습니다. [첫 리뷰](https://github.com/ehojune/bioinfo-team-3d/pull/1495#discussion_r4683505246) · [후속 리뷰](https://github.com/ehojune/bioinfo-team-3d/pull/1495#discussion_r4683883657)
- 제품 코드 순증 43줄입니다. 핵심 세 파일은 `cso.py` +7, `server.py` +5, `daemon.py` 0으로 합계 +12/82줄입니다.
- 최종 CI와 리뷰는 PR 기록에서 확인합니다. [PR #1495](https://github.com/ehojune/bioinfo-team-3d/pull/1495)
