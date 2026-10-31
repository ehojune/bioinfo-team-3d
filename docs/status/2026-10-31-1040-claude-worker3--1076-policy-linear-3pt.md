## 단계
#1076: `test_policy.py`의 경로 스캔 선형성 시험이 Linux 러너에서 8~10배로 반복 실패한다. 근거: [W2 조사 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1076#issuecomment-6536619745).

## 한 일
- `_assert_linear`가 세 점(1/4, 1/2, 전체)의 per-call 비용(가장 빠른 block)을 재고 **2배 증가마다** 비용이 3.2배 이하인지 본다(시계 한 tick 여유는 그대로). 선형은 증가마다 약 2배, 제곱은 약 4배다.
- 시계를 흉내 내는 두 시험(`test_linear_scan_budget_*`)을 세 점에 맞췄다. 선형 2배, 경계 3.2배, 제곱 4배, 첫 증가만 초과, 마지막 증가만 초과, 결과가 틀림, 빠른 기계의 block, 한 tick 안과 밖, 그리고 반만 늦은 호출·한 호출이 멈춘 경우를 담았다. 시험 이름은 그대로라 inventory의 node 목록은 바뀌지 않는다.
- 실제 CPU 제곱 시험 둘(`...still_rejects_a_really_quadratic_scan`, `...slow_only_the_first_time`)은 손대지 않았고 통과한다. `mixed_nodes`의 sha256을 새 파일에 맞췄다(`validate_profile` 통과).

## 확인
- `tests/test_policy.py`와 `test_ci_boundary_preview.py` 333 passed, 6 skipped.

## 주의
- CI의 9.7배(4배 입력)는 크기에 따른 지수 약 1.6이라 증가마다 약 3.1배다. 새 상한 3.2는 그 값에 가깝다. 실제 러너에서 통과하는지는 CI run으로만 알 수 있고, 로컬(Windows)에서는 재현하지 못했다. 다시 실패하면 상한을 올리지 말고 이 러너의 super-linear 원인(메모리·캐시, 아니면 `touches_resolved` 안의 실제 super-linear 단계)을 먼저 재야 한다.
- 이 시험은 지수 약 1.6의 완만한 super-linear 회귀와 러너의 비선형을 구별하지 못한다.

## 리뷰 후 수정 (rev20)
- P1: 시계 한 tick의 여유가 두 단계에 각각 더해져 4, 16, 64 ms로 제곱해 가는 비용이 통과했다. `_assert_linear`가 quarter에서 full까지 4배 구간 전체에도 `3.2² × quarter + 한 tick`을 한 번만 허용한다.
- 시험: `(0.004, 0.016, 0.064)`가 거절되고(단계마다 4배), `(0.004, 0.012, 0.036)`은 통과한다(단계당 3배, 전체 9배). inventory sha256을 맞췄고 `validate_profile`이 통과한다.
- `tests/test_policy.py`와 `test_ci_boundary_preview.py` 335 passed, 6 skipped.
- 주의: 전체 상한 3.2² = 10.24는 CI의 9.7배(4배 입력)와 가깝다. 실제 Linux 러너의 통과 여부는 CI로만 확인한다.
