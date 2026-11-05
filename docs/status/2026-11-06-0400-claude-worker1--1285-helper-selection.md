# #1285 — 변경 helper가 Windows 풀에 끌어오는 시험 범위

- 결론: 선택 규칙이 정확해졌지만, 이것만으로는 #1284 같은 helper 변경의 pool 시간(684 s)이 크게 줄지 않습니다. 무거운 lock 직렬 시험이 그 helper를 실제로 쓰기 때문입니다. 아래 측정 참고.
- 바뀐 것
  - `scripts/ci_selection.py`: "직접 import한 변경 helper" 규칙을 파일 단위에서 **node 단위**로 좁혔습니다. node의 범위(시험, 그 시험이 부르는 지역 helper·fixture, import 시점 코드)가 import한 이름을 쓰는 경우만 선택합니다. fixture 인자, `usefixtures`, helper의 autouse fixture, star import, import 시점 사용은 보수적으로 선택합니다. 파일 단위 결정(node 없음)은 그대로입니다.
  - `scripts/ci_suite.py`: 선택 이유별·파일별 node 수를 `inclusion-<worker>.json`(진단 전용)으로 남깁니다. 단계 증명이 `collection-*.json` 키 집합을 닫아 놓아 별도 파일로 뒀습니다.
  - `config/ci/windows-boundary-v1.json`: 새 시험 파일 등록. `tests/test_ci_cohort823.py`: 옛 규칙("import만 해도 선택")을 확인하던 시험이 helper를 실제로 부르게 고침.
- 실측 (로컬, #578 head 45개 변경 목록으로 CI 아티팩트의 전체 node에 새·옛 규칙 적용)
  - 선택 node: 옛 6328 → 새 5911(−417). 로컬 node 시간 822 s → 787 s(−35 s).
  - host-resource lock 직렬 시간: 231 s → **231 s(변화 없음)**. 무거운 node(`test_semantics_shadow_remove`의 두 node 54 s·52.6 s, `test_e2e_mock` 14.8 s 등)는 `_run_lab`·fixture를 실제로 써서 그대로 선택됩니다.
  - helper 하나(`tests/semantics_shadow_lab.py`)만 바뀐 경우 이 규칙이 끌어오는 node: 옛 209 → 새 120(−43%).
- 한계·다음: pool 시간의 주 병목(lock 직렬 시험)은 이 변경으로 줄지 않습니다. 직렬 시간 예산을 PR 단계에서 미리 알리는 방식은 워크플로 변경이 필요해 이 claim 밖입니다. `inclusion-<worker>.json`이 쌓이면 어떤 변경이 몇 node를 끌어왔는지 아티팩트 한 곳에서 볼 수 있습니다.
- 확인하지 못한 것: 새 head의 실제 Windows pool 시간과 `inclusion-*.json`의 실제 출력은 CI 결과로 확인합니다.
