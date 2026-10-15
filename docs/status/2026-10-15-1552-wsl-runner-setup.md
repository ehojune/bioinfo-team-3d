## 결론

Refs #612. 1단계: `setup-wsl`로 로컬 WSL runner를 준비하고 Windows gateway에 연결합니다. 실행 위치 카드는 2단계로 남깁니다.

## 바뀐 것

- `setup-wsl --check`: 배포판·Java·Nextflow·Docker·bioinfo-agent·labhq·등록·연결을 항목별 표시.
- setup: 사용자 venv·editable 설치, 없는 Nextflow만 설치, Linux 전용 state·설정·작업 폴더. 관리자 명령은 안내만.
- 토큰은 stdin과 600 설정 파일로 전달. Windows `up/down`과 `wsl-runner start/stop`은 기존 supervisor를 재사용.
- bioinfo-agent를 WSL에 배정하고 Windows 재연결로 덮지 않음. doctor WSL 절과 CSO 도구 안내, manual 추가.

## 확인

| 항목 | 결과 |
|---|---|
| 새 mock test 12개 | main `33e9603`: 12 실패(동작 assertion 4, 새 API 부재 8); branch: 12 통과 |
| 관련 pytest | 409 passed, 3 skipped; 전체 suite는 CI |
| Node | `tests/web_agent_capabilities.cjs` 통과 |
| 실제 setup·check | Ubuntu 22.04, 별도 설정·state·임시 포트의 Windows gateway에 `lab-wsl` 연결; 8항목 모두 ok |
| 반복·재시작 | setup 재실행 성공, `down → up` 뒤 WSL 재연결 |
| 도구 | Java 17.0.20.1, Nextflow 24.10.5, Docker 29.7.1 |
| Nextflow + Docker | Ubuntu 22.04 컨테이너의 HELLO 1 task, COMPLETED / exit 0, `hello.txt=hello`, report·trace 생성 |
| 토큰 | Linux 폴더 700·설정 600, Linux 로그 11개와 Windows 로그에서 토큰 0건 |

실측은 mock 직원으로 runner 연결을 확인하고 Nextflow를 WSL에서 직접 실행했습니다. WSL Claude CLI·직원 로그인은 없어서 bioinfo-agent의 LLM 실행은 검증하지 않았습니다. 초기 Alpine 컨테이너에는 Bash가 없어 실패했고 Ubuntu 이미지로 완료했습니다. PI 인스턴스·PI venv·보호 포트·HPC·관리자 명령은 사용하지 않았습니다.

## 남은 것

- WSL Claude CLI와 직원 전용 로그인 준비. setup은 자격 증명을 입력하지 않습니다.
- CI 결과는 PR checks에서 확인. 리뷰 요청·병합은 하지 않습니다.
- 로컬 실측 근거: `.pr-drafts/live-evidence.json`, `hello.log`, `live-setup-wsl-check.log`, `test-main.log`, `test-final.log`(모두 커밋 제외).
