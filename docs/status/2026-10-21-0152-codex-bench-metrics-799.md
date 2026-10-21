Refs #799, #739

bench별 기록기 연결을 로컬 구현했습니다(76437b0). 실제 bench·연구·속도/효용 검증은 하지 않았습니다.

- `bench_metrics.py`가 기존 #815 계획 모양 셈법을 공유하고, hash 일치 승인 snapshot·task ledger·결과/승인 event를 arm별로 집계합니다. 최초/재계획/continuation 기대 dispatch, hosted task·attempt retry·consult·skill·분석·review·보고·승인 대기, known 비용·unknown 개수를 구분합니다.
- bench가 만든 runner에만 opt-in callback을 연결합니다. 성공한 OS spawn 뒤 시작 수와 argv UTF-8 표현·stdin byte만 셉니다. 단독 엔진도 직접 계측합니다. task/prepared prompt·context 크기는 대리값이며 원문·명령·경로·secret·다른 요청은 새 receipt에 저장하지 않습니다.
- 누락은 `not_collected`, 단독 arm의 labhq 단계와 mock의 실제 CLI는 `not_applicable`입니다. 실패 spawn과 미관측 attempt를 구분하고 재전송을 중복 집계하지 않습니다. `run.json`·arm별 `bench_metrics.json/.md`·comparison에 전달하며 재채점은 관측과 sidecar를 보존합니다.
- 집중 fake/static 28건과 실제 `result_editor` kind 연결 1건 PASS. Python 3.10 AST/compile 6파일·변경된 비-bench runtime 2파일 정답 누출 정적 검사·public·diff·patch-notes·notes 검사를 기록했습니다. Reference LF 문자 수 4833→4780(-53).
- 기존 `test_bench_answer_identifiers_do_not_leak_into_non_bench_runtime_files`는 내부 git subprocess fixture가 mock guard에 차단됐습니다. 로그/실패명을 보존하고 재실행하지 않았으며 동일 검사 함수의 위 두 파일 정적 대조로 대체했습니다. 원 pytest node와 Linux/Windows 실제 process 통합은 CI 경계입니다.
- 남은 범위: canonical 승인 snapshot이 없는 일반 lane 계획·재계획 rerun 증거, 실제 파일/환경·SDK 내부 전달량·baseline 내부 turn은 미수집입니다. launch 성공은 내부 CLI 인증·tool 성공의 증거가 아니며 단계 시간은 중첩되는 task elapsed 합입니다. #799는 계측 공백과 승인된 실제 비교가 남아 OPEN으로 둡니다.
- CSO 정책·승인/hash·DB schema·CI/conftest/pyproject·의존성·PI 상태는 변경하지 않았습니다. 실제 process/thread/포트·설치·PI·연구·bench·HPC·원자료·network fixture 미실행. #793 정책 준비 전까지 로컬 커밋만 보존하고 push/PR·CI polling·병합·배포를 하지 않습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
