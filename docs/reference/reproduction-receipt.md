# 재현 검증 receipt — lookup-only

[요구사항 #748](https://github.com/ehojune/bioinfo-team-3d/issues/748), [PI 창구 #760](https://github.com/ehojune/bioinfo-team-3d/issues/760). 코드: `labhq/reproduction_receipt.py`.

| 항목 | 기록·한계 |
|---|---|
| 범위 | 전달받은 공개용 소스·lock만 임시 폴더에 복사. Python compile, R parse, bash -n은 코드를 실행하지 않음 |
| 판본·경로 | 도구 이름·숫자 판본, publication-relative cwd·명령·예상 출력, 코드/lock SHA-256, UTC 검사 시각 |
| 입력 | 코드가 참조한 변수 이름과 명시적으로 제공한 값의 유무만 검사. 호스트 환경값을 물려주거나 원자료 존재·권한을 탐색하지 않음 |
| 상태 | 문법 pass/fail/unavailable/timeout, 기본 실행 not_rerun, 누락 입력 blocked_input. 누락 lock·건너뛴 소스도 표시 |
| 실제 실행 | 별도 `rerun=True` 승인 호출만 수행. 이번 검증은 작은 fixture뿐. 실행 pass는 exit 0 + 선언한 출력 파일 존재이며 수치·과학 결과의 동등성 검증은 아님 |
| Python flag (#772·#794) | 문법 검사 `-I -S -B -c compile`(import 없음). 실행 `-E -s -B <script>`: script의 staged 폴더와 실행 interpreter의 site-packages는 import 경로에 둠. `PYTHONPATH` 등 `PYTHON*` 환경(입력으로 넘겨도), 사용자 site, 호출한 폴더, 다른 step 폴더는 제외. `-S`를 두면 venv·conda의 numpy 등이 사라져 실제 분석 rerun이 모두 fail이었음(#794). 실제 interpreter 검증은 mock guard가 없는 CI의 `test_a_real_isolated_rerun_imports_site_packages_and_siblings_but_not_the_callers_paths` 하나(tmp fixture만)이며 실제 분석 rerun PASS가 아님 |
| 실패 | Python 문법 실패는 기존 게시 차단을 유지하고 receipt를 저장 상태에 수집. R/bash 실패·도구 없음도 보고서에 표시 |
| 소비자 | 정리본, 공통 ReportPackage, bench run/score/comparison. 없는 receipt는 not_checked/not_rerun이며 성공으로 추정하지 않음 |

2026-10-20 총괄 인계: 10-19 실제 사용량 11,376,303/14M, 미사용 2,623,697을 M 단위로 올리면 3M이다. 여기에 4M을 더해 **10-20 한도는 7M으로 확정**했다(이전 2M 대체). 개발 위임 범위의 기록이며 분석·다운로드·기관 접근·HPC·게시 승인이 아니다.
