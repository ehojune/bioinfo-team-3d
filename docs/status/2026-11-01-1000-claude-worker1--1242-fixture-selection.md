# #1242 — fixture만 바뀐 PR이 그 fixture를 쓰는 Windows 시험을 고르지 못하는 구멍

- 결론: Windows 시험 선택(`scripts/ci_selection.py`의 `include_windows`)이 Python import만 따라가서, 시험이 subprocess로 돌리거나 파일로 읽는 `tests/fixtures/` 파일이 바뀌어도 그 시험이 선택되지 않았다. 사례 하나씩 등록하지 않고 부류를 닫았다.
- 규칙(`changed_fixtures`·`references_fixture`·`_fixture_reason`): 바뀐 경로 중 `tests/fixtures/` 아래 파일마다, 시험 소스가 그 파일의 경로·파일 이름·모듈 이름(짧거나 `__init__` 같은 이름은 제외)을 문자열로 적고 있으면 그 시험을 고른다. 이름을 적은 시험이 하나도 없으면(`tests/test_*.py` 전체를 한 번 훑어 확인) 짐작하지 않고 모든 routine Windows 시험을 고른다. 넓게 맞추는 쪽이라 이름만 언급한 시험도 선택될 뿐, 빠지는 쪽으로는 틀리지 않는다.
- 바꾼 것: `scripts/ci_selection.py`, `tests/test_ci_parallel_policy.py`(+5건). `scripts/ci_suite.py`는 건드리지 않았다. "아무도 이름을 안 적은 fixture"의 전체 선택을 `select()` 쪽이 아니라 `include_windows`에서 한다(claim 범위 때문이기도 하고, 시험 한 건마다 판정이 같아져 설명이 쉽다).
- 시험: 경로·파일 이름·모듈 이름으로 선택, 변경이 없으면 제외, 다른 시험이 이름을 적었으면 이 시험은 제외, 아무도 이름을 안 적으면 전부 선택, `tests/fixtures/` 밖의 파일은 fixture로 보지 않음, 그리고 실제 사례(PR #1235: `tests/fixtures/fake_deadline_cli.py`만 바뀜)가 `tests/test_request_deadline_descendants_1232.py`를 고름. 모든 `tests/test_ci_*.py` 1263건 통과. 실제 정책으로 이 fixture만 바뀐 경우 선택되는 시험은 2건, 선택 판정 비용은 약 1.3초(시험 파일 400개 기준).
- 미검증: 실제 PR의 Windows CI에서 선택이 바뀌는지(이 PR은 fixture를 바꾸지 않아 이 규칙이 켜지지 않는다). 비 Python fixture를 시험이 디렉터리 glob으로만 읽는 경우는 이름이 없어 "아무도 이름을 안 적음"으로 처리되어 전체가 돌아간다(안전한 쪽).
