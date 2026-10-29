## 단계
Paper2Agent 설치본 Windows 경로 패치 — Closes #662.

## 한 일
- `labhq setup-paper2agent`(와 `doctor --fix`)가 설치본 `paper2skill/scripts/reading_package.py`의 `str(...relative_to(...))` 네 곳을 `.as_posix()`로 고칩니다. 두 엔진 사본을 교체하기 전에 staging 복사본에서 적용합니다.
- 결과를 `.labhq-source.json`의 `patches`에 남깁니다(patch id, 파일, 고정 upstream commit `8c2d0591…`, `applied`/`already_patched`/`file_absent`).
- 이미 고쳐진 사본은 그대로 둡니다. 문구가 맞지 않거나 일부만 고쳐진 사본은 `RuntimeError`로 설치를 멈추고 기존 설치를 보존합니다.
- `labhq doctor`는 패치되지 않은 옛 설치본을 준비 안 됨으로 보고하고 `--fix`가 다시 설치합니다.
- 매뉴얼 해당 절을 고쳤습니다.

## 테스트 결과
- `tests/test_paper2agent_winpath_662.py` 6건: 네 곳 적용·provenance, Windows 경로(PureWindowsPath)에서 패치 전 키 불일치→패치 뒤 일치, 재적용 무변화, 불일치 upstream 실패와 기존 설치 보존, 반만 고쳐진 사본 실패, 스크립트 없음 기록·옛 설치본 감지.
- 관련 test 126건 통과(`test_contract_provenance`의 marker 동등 비교 2곳은 `patches` 키를 제외하도록 고침).
- 미검증: 실제 upstream 설치본 위 적용, 실제 논문 스킬 빌드(범위 밖). `tests/test_doctor.py::test_doctor_warns_about_parent_session_markers_and_pi_skills`는 변경 없이도 이 세션에서 실패합니다(세션 환경 변수).

## 바꾼 파일
labhq/recruit/paper2agent.py, labhq/doctor.py, docs/manual.md, tests/test_paper2agent_winpath_662.py, tests/test_contract_provenance.py와 변경 기록.

## 막힌 점
없음. upstream PR은 선택 범위라 내지 않았습니다.

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
