# #1388 r2 — 독립 경로 삭제안

- draft/hold 준비. 소유 경로의 그림자·객체 뷰·액션·A/B·새 장치 보고서 생성을 제거했다.
- core input-fit·CP1/CP2·typed-output·continuation·공개 안전성 assertion을 보존했다. legacy 두 설정 키만 비활성 입력으로 받는다.
- 전체 수집 15,642건, Node 웹 8시험 통과. 관련 회귀는 진행 중이며 미소유 경로의 남은 호출은 통합 대상이다.
- README·매뉴얼 19파일: 309,559자 → 281,963자(초기 정리 실측).
- server·portfolio·Windows inventory와 추가 경로는 다음 claim이 필요하다. [정확한 목록·근거](../reference/prune-semantics-1388.md).
- 최종 전체 CI·같은 head 리뷰 P1=0·mock 종단·#1375 비교 전 hold 유지. 실제 모델·PI/HPC 실행 없음.
