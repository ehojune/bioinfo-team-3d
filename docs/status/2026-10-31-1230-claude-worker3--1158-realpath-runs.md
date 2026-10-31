## 단계
#1158: `os.path.realpath`가 구분자 run에서 제곱이라 정책 검사가 길어지는 결함. 근거: [#1152 댓글](https://github.com/ehojune/bioinfo-team-3d/pull/1152#issuecomment-6537401540), [두 번째](https://github.com/ehojune/bioinfo-team-3d/pull/1152#issuecomment-6537446112).

## 한 일
- `labhq/policy.py`: `_collapse_separators`(POSIX 호스트에서 `/` run을 하나로, 앞머리 정확히 둘은 보존, 셋 이상은 하나, Windows 경로는 그대로)와 `_realpath`를 두고 `os.path.realpath`를 부르던 다섯 곳(`_real`, 쓰기 루트 두 철자, `_root_rules`, Windows 8.3 해석, 읽기 철자)이 모두 이것을 쓴다.
- 시험(`tests/test_policy.py`): 병합 규칙, Windows 경로는 건드리지 않음, 병합이 `posixpath.realpath`의 답을 바꾸지 않음(길이 6까지 모든 조합), 정책 `_real`의 선형성(Linux에서 실패하던 시험), 병합 유무에 판정이 같음(5개 철자). `mixed_nodes` sha256을 맞췄다.

## 실측
| (WSL Python 3.10, `touches_resolved`, 이 시험의 입력) | 1/8→1/4 | 1/4→1/2 | 1/2→전체 | 1/4→전체 | 전체 1회 |
|---|---|---|---|---|---|
| 수정 전 | 2.30~2.41 | 2.63~2.65 | 2.67~2.72 | 7.04~7.20 | 0.151 s |
| 수정 후 | 1.92~2.02 | 1.93~2.04 | 2.00~2.04 | 3.92~4.09 | 0.065 s |
- 수정 후 증가마다 2.0배, 전체 4.0배로 선형이고, 전체 비용도 0.151 → 0.065초다.
- 판정은 Windows와 Linux(WSL) 모두 병합 유무에 같다(8개 철자 확인).

## 확인
- Windows: `test_policy.py`·`test_ci_boundary_preview.py`·`test_policy_scan_cost.py` 377 passed, 6 skipped. `validate_profile` 통과.
- Linux(WSL 3.10, httpx 없음): `test_policy.py` 291 passed, 1 failed(`test_single_recursive_delete_below_step_tmp_is_allowed[rm -rf .tmp]`). 실패는 `httpx` 모듈이 이 환경에 없어서이고 이 변경과 무관해 보이나, 같은 환경의 main에서는 확인하지 않았다.

## 하지 않은 것
- Python 3.12의 Linux 구현은 못 재고 `posixpath.realpath`를 Windows에서 돌려 같은 제곱임을 확인했다.
- Windows 경로는 realpath에 제곱이 없어 건드리지 않았다.
- #1152(3점 비교)는 이 결과를 보고 닫거나 줄인다.
