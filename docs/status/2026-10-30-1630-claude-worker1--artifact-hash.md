# #1058 상류 산출 hash 누락 (② 5차)

- 결론: 두 증상은 같은 뿌리가 아니었다. 수치가 UNVERIFIED로 남은 것은 실제 결함(A)이고, 이어 가기 재사용 거부는 설계상 거절(B)이다.
- A (고침): 5차 s1 계획이 폴더 `outputs/literature`를 산출로 선언했다. 러너는 이 폴더를 `result.outputs`에 hash 없이 넣었고(5개 중 4개만 hash, gateway의 라운드 기록 근거), `step_inputs.stage_inputs`가 "collected artifact hash is missing"으로 s1 전체를 거절해 s2에 상류 산출이 전달되지 않았다.
- 바꾼 것: `runner/daemon.py`는 선언된 폴더를 그 안의 파일(산출 후 hash가 있는 것만)로 풀어 수집하고, 필수·선택 산출 검사에서 폴더 선언은 안에 수집된 파일이 있으면 충족으로 본다. `orchestrator/cso.py`의 `apply_declared_output_status`도 같은 규칙이다. 시험 3건(`tests/test_observed_outputs.py`).
- 동작 변경: 비어 있거나 hash를 만들 수 없는 폴더(너무 큼·링크뿐)는 산출로 치지 않아 단계가 `incomplete: missing outputs`로 끝난다. 전에는 통과한 뒤 하류에서 깨졌다. README 한·영에 적었다.
- B (이번 범위 밖): 요청 `references`에 DOI 같은 URL형 참조가 있으면 `research/input_proof.capture`가 내용 영수증을 만들 수 없어 None을 돌려주고, 이어 가기가 "request-level input hash record missing"으로 재사용을 거절한다. 5차 요청이 DOI 참조를 가져 항상 걸렸다. 정책 변경은 별도 issue.
- 실행한 것: 관련 시험 630여 건 통과(runner·step inputs·연속·cso).
- 미검증: 실제 연구 요청에서 s1 → s2 전달 end-to-end(#910 6차에서 확인).
