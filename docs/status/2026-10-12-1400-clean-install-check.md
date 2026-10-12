# v0.95 깨끗한 설치 리허설

## 결론

PR #536에서 새 Windows clone·venv의 설치부터 mock 요청, 보고서·요청 묶음, `verify`, 종료·정리까지 자동화했습니다. 리허설 차이 3건을 찾아 3건 모두 고쳤고 CI 전체가 통과했습니다.

## 바뀐 것

- `scripts/clean_install_check.py`: 임시 HOME·state·빈 포트·mock 직원으로 전 과정을 검사하고 삭제
- README·manual: 빠진 질문과 격리 설치 검사 명령, v0.95 현재 상태 보정
- `.github/workflows/test.yml`: 기존 suite와 병렬인 Windows `clean-install` job 추가

## 리허설 차이

| 단계 | 문서가 말한 것 | 실제 | 고침 |
|---|---|---|---|
| `labhq init` | HPC·bioinfo-agent 경로를 질문 | 선택형 AlphaGenome 키 질문도 나옴 | README에 Enter로 생략한다고 명시; 검사 `labhq init` 단계가 질문 수를 고정 |
| 설치 완주 | 명령을 따로 실행 | mock으로 요청·묶음·검증까지 반복할 검사 없음 | `clean_install_check.py`의 요청·묶음·`verify`·`down` 단계와 Windows CI 추가 |
| Windows CI 출력 | 실패 단계와 출력을 한 줄로 표시 | cp1252 console에서 한글 단계명 출력이 실패 | stdout·stderr·자식 출력을 UTF-8로 고정하고 encoding 회귀 test 추가 |

## 확인

- 수동 리허설과 자동 검사 통과. `doctor` fail 0·실행 준비 예, `verify` exit 0
- PI 홈과 본 checkout 설정 mtime: 전후 동일
- README: 전체 23,938 → 23,931 bytes, 설치 절 420 → 413 bytes
- 관련 pytest: 114 passed, 1 skipped; README test: 33 passed
- Windows 전체 pytest: 첫 실행 5,325 passed·66 skipped·Windows manifest 교체 flake 1건; 실패 test 재실행 1 passed; 전체 재실행 5,326 passed·66 skipped
- cp1252 강제 clean install·encoding 회귀 test: 통과
- `scripts/check_public.sh`, `git diff --check`: 통과
- CI run 38206716903: changes·patch-notes·clean-install·pytest 3.10·3.12·Windows·slow·ro-crate-validator 성공; README 전용 job 조건부 skip

## 미해결

- 없음

Refs #300

🤖 Generated with Codex (gpt-5) for the labhq dev lead
