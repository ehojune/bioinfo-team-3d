## 결론

PR #759의 CI 실패 4건을 수정하고 main의 편집기 보정을 합쳤습니다. Refs #757.

## 바뀐 것

- 호환용 `report.md`도 내부 claim을 독자용 근거 번호로 변환합니다. 게시 검사와 승인 게이트를 유지합니다.
- binary 읽기를 기존 인코딩 검사가 판별하는 호출로 맞췄습니다.
- main의 PDF 각주·질문 정리와 CommonMark 코드 영역 보호를 함께 보존했습니다.

## 확인

- 기존 실패: 수정 전 4 failed. 추가 회귀: 수정 전 1 failed, 2 passed.
- 수정 후 관련 pytest 178 passed, main 통합 후 200 passed.
- 공개 저장소 검사와 `git diff --check` 통과.

## 남은 것

- 최종 head의 CI 결과는 PR에 기록합니다. 리뷰 요청과 병합은 총괄이 이어받습니다.
