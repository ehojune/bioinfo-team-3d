## 결론

PR #762의 P1 4312241631 수정을 확인하고 새 P1 4314252458을 `2ca1543e`로 고쳤습니다. Fixes #732. Refs #760.

## 바뀐 것

- 승인 전에 공개용 reference 정리를 적용해 소스 전문·fingerprint·대상 repo/base와 업로드를 맞췄습니다.
- 원본·최종 후보의 공개 검사를 유지하고 긴 pipeline 소스에 보고서 길이 제한을 적용하지 않습니다.
- nf-core 우선 배정·건별 승인·CP1/CP2를 유지합니다.

## 확인

- 새 회귀 3개: 수정 전 실패 → 수정 후 통과. 관련 pytest 130개, 별도 source/destination 검증 6개, CP1/CP2 5개 통과.
- Node 상태 회귀·승인 카드 13개·후보 소스 DOM 검증 통과. script/HTML은 문자열로 표시되고 소스 끝까지 펼쳐집니다.
- 승인 전·거절 후 mock GitHub 호출 0회, yes 뒤 PR 1회와 재시작·중복 복구를 확인했습니다.
- 공개 검사·Python 3.10 AST·diff 검사 통과. manual 160,409 → 160,459자.

## 남은 것

- P2 4312241622: init 질문·manual 앞부분·example config의 일회성 동의 문구.
- 새 head CI와 병합은 총괄이 확인합니다. 리뷰 재요청·병합·배포·실제 분석·외부 기여 PR은 실행하지 않았습니다.
- UI는 Node DOM으로 검증했습니다. 실제 브라우저 화면은 UNVERIFIED입니다.
