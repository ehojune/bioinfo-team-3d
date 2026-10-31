# #1127 validation-manifest가 방금 끝난 Windows job을 못 찾는 문제

- 결론: `scripts/ci_skip.py`의 `windows_artifact`가 effective attempt의 `pytest-windows`가 **아직 목록에 없거나 완료되지 않은** 동안만 3초 간격으로 최대 40번(약 2분) 다시 읽는다. 완료된 뒤의 검증(`conclusion`, run·attempt·head 묶기, artifact 시각 포함)은 그대로이고, 완료된 실패·2개 이상 겹침·묶기 실패는 기다리지 않고 즉시 실패한다. 상한을 넘으면 같은 `missing/ambiguous Windows producer` 오류가 난다.
- 원인(정황): run 41111652681에서 attempt 2는 Windows 완료 3초 뒤에, attempt 3은 성공 job의 복사본이 만들어지기 7초 전에 manifest가 목록을 읽었다. 실패 순간의 API 응답은 보지 못했다.
- 시험: `tests/test_ci_manifest_identity.py`에 다섯 경우(늦게 나타난 복사본, 진행 중이던 job, 상한 끝의 같은 오류, 완료된 실패 즉시 거부, 2개 즉시 거부). 기존 `select` 도우미는 sleep을 stub해 시험이 실제로 기다리지 않는다.
- 바꾼 것: `scripts/ci_skip.py`, `tests/test_ci_manifest_identity.py`. `validate_profile()` 통과, `config/ci/windows-boundary-v1.json`은 건드리지 않았다(이 시험 파일은 sha 고정 대상이 아니다).
- 미검증: 실제 GitHub 재실행에서 2분이 충분한지, 재시도가 API 호출 한도에 걸리는지.
