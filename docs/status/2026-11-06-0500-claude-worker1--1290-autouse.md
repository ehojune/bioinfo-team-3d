# #1290 — 비상수 autouse를 helper 선택에서 놓치던 경우

- 바뀐 것: `scripts/ci_selection._scope_index`가 `autouse` 키워드 값이 **리터럴 `False`가 아니면** 모두 autouse로 봅니다(`autouse=AUTO` 같은 상수 변수·표현식 포함). helper의 autouse 판정과 시험 파일 안의 지역 autouse 판정이 같은 함수라 둘 다 보수적으로 바뀝니다. 선택이 줄 수 있는 쪽만 막는 변경이라 더 많이 선택하기만 합니다.
- 시험: `tests/test_ci_helper_selection_1285.py`에 5건(`True`·`AUTO`·`not False`·`settings.auto()` 선택, 리터럴 `False` 제외)과 지역 autouse 1건을 추가. CI 관련 시험 1339 passed, 1 skipped(로컬 Windows).
- 영향: #578 변경 목록을 쓴 실측 선택 node는 새 규칙 전후가 같은 5912개입니다(이 repo에는 비상수 autouse가 없음).
- 한계: 값이 `False`로 평가되는 비상수 표현(`autouse=0` 등)도 선택합니다. 조금 더 선택할 뿐 놓치지는 않습니다.
