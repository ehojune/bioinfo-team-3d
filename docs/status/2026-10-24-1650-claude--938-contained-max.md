Closes #938

contained runtime 확인(`doctor --runtime`의 `contained_verified`)이 owner receipt 수명을 발급 후 5400초(90분)로 고정해 15시간 연구 실행(#910 6차)을 막았습니다. 그 상한을 설정값으로 만들었습니다.

- 설정 `runner.contained_max_s`: 기본 5400(기존 동작 유지), 하한 60, 상한 86400. 범위 밖 값은 설정 로드 때 거부합니다. `labhq/runtime_readiness.py`는 `deadline <= issued + contained_max_s`로 검사합니다.
- 다른 확인 조건(nonce·provenance·config hash·pipe·운영자 pin·owner 응답 마감 일치)은 건드리지 않았습니다.
- **발급 쪽:** receipt를 발급하는 contained owner는 이 저장소 밖(운영 harness)에 있어 코드로 맞추지 못했습니다. 15시간으로 쓰려면 owner도 같은 값(예: 54000 이상 설정값)으로 발급해야 하고, 아니면 이 검사가 거부합니다.
- 테스트(`tests/test_contained_readiness.py`): 기본 5400 통과·5401 거부, `contained_max_s=54000`에서 54000 통과·54001 거부, 86401·59·0·-1 설정 거부. 수정 전 코드에서는 54000 경계 테스트가 실패하는 것을 확인했습니다. 같은 파일 56건 통과(로컬, Windows, Python 3.12).
- 문서: `docs/manual.md` doctor 절, `docs/reference/contained-runtime.md`.
- 미검증: 실제 15시간 owner·새 설치판·종료 증명. `tests/test_doctor.py::test_doctor_warns_about_parent_session_markers_and_pi_skills`는 이 환경에서 main에서도 실패합니다(무관).

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
