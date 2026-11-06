## 단계
#1300 rev5: 사이트 프로필 질문의 불리언 기본값 버그(PR #1304 리뷰에서 발견).

## 한 일
- `labhq/hpc_site.py`: `remote_exec`·`agent_inside_cluster`의 기존 값을 `y`/`n`으로 보여 준다. init의 `_ask`는 Enter에 보여 준 문자열을 돌려주므로, 전에는 `True`/`False`가 와서 `y/n`만 받는 검사에 걸려 다시 물었다. `true`/`false` 답도 받는다.
- `tests/test_hpc_site_profile_1300.py`: Enter만 누르면 모든 기존 답이 유지되는지(참·거짓 포함), `true`/`false` 허용, 실제 `_ask`(입력 가로채기)로 init 흐름 3건 추가.
- manual 04장 한·영: 알려진 한계 문장을 지우고 `y`/`n`·`true`/`false` 안내로 바꿨다.

## 확인
- `tests/test_hpc_site_profile_1300.py` 67 passed(Windows 로컬).

## 하지 않은 것
- 다른 질문 형식(숫자·선택)은 이미 Enter로 유지됐고 바꾸지 않았다.
