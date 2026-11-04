## #823 — 진단 검토 P2 반영과 main 통합 (source 07cbf33, merge 1d6352c)

- 독립 검토(READY739 `out-ci823-peer0442.md`, 대상 `6bddac7`)는 P1 0건이었다.
- P2-1: 진단 중복 키를 잘린 nodeid 대신 원본 nodeid의 sha256(`nodeid_sha256`)으로 바꿨다. 앞 512자가 같은 서로 다른 긴 parameter 실패도 따로 남는다. 출력 nodeid는 그대로 512자로 자른다.
- P2-2: crash 첫 줄은 앞 4096자만 검사하고 300자만 기록한다. `check_public.sh`가 막는 credential 형식(GitHub·`sk-`·`AKIA`·`AIza`·`xox*`·private key)과 Bearer 값을 고정 패턴으로 가린다. 패턴은 literal prefix와 단일 문자 class만 쓴다.
- 진단 파일이 proof·metrics·receipt·재사용 판정과 분리된 점, 실패 50·slow 30 상한, 5초 갱신은 그대로다.
- `origin/main` `ecf1c1b`(#837 `10303fd` 포함)를 명시적으로 merge했다. 들어온 것은 공개 검사 4파일과 생성 목차 2파일뿐이고 충돌은 없었다. 제품 코드 변경은 없다.
- 로컬 검사(guard 유지, blocked 0):
  - `tests/test_ci_windows_phases.py` 114 passed(긴 id 구분, credential 8형식, 20만 자 한 줄 포함).
  - CI 정책 검사 4파일 381 passed.
  - Python 3.10 AST, `check_public.sh`, `git diff --check`, `notes_index --validate`, `patch_notes check` 통과.
- 미실행: consult case와 Windows 실제 진단 기록은 CI에 맡긴다. #837의 원래 예외는 미확인이다.
