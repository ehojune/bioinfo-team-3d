## 요약

- direct 응답이 기존 `declare_output_types` 어휘로 실제 `outputs/` 산출의 데이터·형식 종류를 함께 선언합니다.
- runner가 기존 파일 상한·허용 구역·정규 파일 검사 뒤 해시와 종류 기록을 남기며, 요청 묶음·RO-Crate·`labhq verify`의 기존 direct 산출 경로를 그대로 씁니다.
- 두 번째 direct 요청의 재사용 후보는 첫 산출에 종류 선언이 있을 때만 잡히고, 없으면 `type_unknown`을 유지합니다.

## 검증

- `origin/main` + 새 회귀검사: 2 failed, 52 passed, 2 skipped
- 관련 semantics·bundle·verify 회귀: 721 passed, 6 skipped
- Windows 전체 pytest: 5020 passed, 64 skipped
- `bash scripts/check_public.sh`: 통과

## 문서

- `docs/manual.md`: 104,097자 → 105,268자(+1,171자)

Closes #221

🤖 Generated with Codex (gpt-5) for the labhq dev lead
