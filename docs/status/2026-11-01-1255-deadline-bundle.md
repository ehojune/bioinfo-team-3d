# #1255 기한 취소와 요청 묶음

- 취소된 단계의 완료 결과가 task ledger에만 남아 묶음에서 빠지는 문제를 수정했다. 기존 단계 결과는 보존한다.
- 기존 결과와 취소 revision은 별도 경로에 보존한다. 같은 workdir과 다른 workdir, 같은 파일명도 확인했다.
- 회귀시험은 수정 전 실패했고 수정 후 통과했다. `tests/test_request_bundle.py`: 114 passed, 2 skipped.
- 실제 모델·원격 runner·HPC와 PI instance는 검증하지 않았다. 취소 대기 시간 안에 보고되지 않은 결과는 이 시험 범위 밖이다.
