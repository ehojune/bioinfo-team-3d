## 결론

내부 자료의 행 출력 명령을 승인 카드에서 구분하고, 직원 단계에 메모리 상한을 적용했습니다. Fixes #706. Fixes #709.

## 바뀐 것

- 내부·제한 구역의 행 출력은 `내부 자료 행 출력`으로 표시하고 거절을 권고합니다. 머리글·열 이름·행 수·집계와 공개 경로는 제외합니다.
- 기본 상한은 가용 물리 메모리의 60%이며 0이면 끕니다. Windows는 Job object, POSIX/WSL은 `RLIMIT_AS`를 씁니다.
- 상한 초과 원인과 조각 처리·묶은 이항 GLM 안내를 직원 실패 결과에 남깁니다.

## 확인

- 새 테스트는 구현 전 collection error 2건, 구현 뒤 30 passed입니다.
- 관련 pytest는 553 passed, 14 skipped입니다. Windows Job object 실제 강제도 확인했습니다.
- `scripts/check_public.sh`와 기록 검사를 통과했습니다.
- `docs/manual.md`: 155,842자 → 156,097자(+255).
