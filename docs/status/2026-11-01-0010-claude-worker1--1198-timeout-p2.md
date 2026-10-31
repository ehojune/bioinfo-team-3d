# #1198 시험 상한 P2 두 건(#1199 리뷰)

- 결론: (1) 예고 타이머가 전역 ini(300초)만 보던 것을 `item_limit`로 바꿨다. pytest-timeout의 순서(가까운 `@pytest.mark.timeout(n)` → `--timeout` → `PYTEST_TIMEOUT` 환경 변수 → ini)를 그대로 따르고, marker가 0이면 상한이 없는 것으로 본다. 잘못된 값은 0(타이머 없음). (2) Windows critical 단계(step 상한 3분)에 `PYTEST_TIMEOUT: '150'` 환경 변수를 줘 시험 상한이 단계보다 먼저 오게 했다. phase 수집 필터가 거부하는 `-o`를 쓰지 않는다.
- 확인: 임시 시험(8초 잠)에 `PYTEST_TIMEOUT=2`를 주면 3초 안에 `Timeout`으로 끝나고 스택이 찍힌다(환경 변수가 실제로 적용됨). `tests/test_ci_suite.py` 신규 5건(수정 전 전부 실패)과 기존 포함 16건 통과.
- 바꾼 것: `scripts/ci_suite.py`, `.github/workflows/test.yml`, `tests/test_ci_suite.py`, `docs/reference/ci-reuse.md`.
- 한계: critical 단계의 150초는 그 단계 시험 셋(WMI bootstrap 몇 건, 브라우저 1건)의 측정 최악값(약 10~40초)보다 충분히 크게 잡았지만 새로 재지는 않았다. pool 단계(13분)는 기존 300초 그대로다.
- 미검증: Windows CI에서 critical 단계가 실제로 이 상한으로 이름과 함께 끊기는지(멈춘 시험이 있을 때만 드러남).
