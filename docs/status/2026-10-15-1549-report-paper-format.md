## 결론

Refs #609. 운전 기록과 과학 보고서를 분리하고 일반·연구·solo 보고서의 기본 형식을 논문 순서로 바꿨습니다.

## 바뀐 것

- 과학 본문: 제목·요약·서론·방법·결과·논의·결론·참고. 별도 과학 부록에는 점검표·리뷰·가정만 둡니다.
- 운전 정보는 `execution_record`·`execution_record.md`로 이동합니다. 라운드에는 과학 본문 대신 묶음·게시 정리본·Nextflow 산출 링크를 남깁니다.
- 쓰기 가능한 최종 보고서 단계가 graphical abstract를 만들고 CSO는 첫 그림으로 링크합니다. 기존 자동 흐름도와 #608 PDF 정책은 유지합니다.
- `edition_sources` 출처 추출을 참고 절에 재사용합니다. PDF·편집자·검색·verify·CP4·웹·CLI 호환을 확인했습니다. 옛 CP4 초안의 hash는 유지합니다.

## 확인

- 새 pytest 11건: main `33e9603`에서 11 failed → branch에서 11 passed.
- 관련 pytest: 중복 제외 1,119 passed, 5 skipped. Node 63개 파일 통과. 전체 pytest는 CI에 맡깁니다.
- plan/research prompt hash는 #609 규칙 추가에 맞춰 갱신했습니다. 기존 검사는 내용·한도·가림을 유지하고 운전 정보의 저장 위치만 바꿨습니다.
- manual: 143,262 → 143,367자(+105). 공유 STATUS·패치노트 목차는 수정하지 않았습니다.

## 남은 것

- CI는 push 뒤 확인합니다. 브라우저 시각 확인은 UNVERIFIED이며 웹 검증은 Node 테스트입니다.
- 리뷰 요청·대기·병합은 하지 않습니다.
