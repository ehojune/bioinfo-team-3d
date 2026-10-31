# #1225 — AlphaGenome 키 파일이 있는 PC에서 annot 직원 실행이 막힘

- 결론: Atlas 시작 점검이 "launch 설정 문자열에 환경 변수 이름 `ALPHAGENOME_API_KEY`의 글자가 있으면 거절"이었는데, `labhq init`이 두는 키 파일 `~/.labhq/secrets/alphagenome_api_key`의 경로가 `labhq_annot` 서버의 `LABHQ_PRIVATE_PATHS`와 Claude 설정의 deny·ask 규칙에 들어가서 키 값이 없는데도 거절됐다. 점검의 예외를 **설정된 키 파일 경로 하나**로 좁혔다.
- 판정(`labhq/adapters/base.py`의 `names_atlas_key`와 `atlas_key_file_forms`): launch 설정(엔진 인자·prefix, MCP 서버 환경, Claude 설정)을 구조째 훑어 문자열마다 예외를 지운 뒤, 남은 곳에 변수 이름 글자가 있으면 어떤 모양이든 거절한다. 예외는 두 가지뿐이다. 설정된 키 파일 경로(`annot.alphagenome_key_file`의 원문·`~` 펼친 것·설정 기준 절대 경로·두 슬래시 표기)와, labhq가 그 경로로 쓰는 Claude 규칙(`policy.claude_deny_private`가 만든 `Read(//c/Users/x/.labhq/secrets/…)`·`Bash(*.labhq/secrets/…*)` 등)이다. 규칙은 그 함수에서 가져오므로 규칙 문법이 바뀌어도 같이 따라가고 철자를 추측하지 않는다. 키 값이 설정에 있으면 이름과 상관없이 거절하는 것은 그대로다.
- 첫 시도(경로 구분자를 앞에서 보고 경로를 가리는 방식)는 PowerShell `Env:\ALPHAGENOME_API_KEY` 설정 형식을 경로로 오인해 통과시켰다(#1228 리뷰 P1). 경로 모양으로 판정하는 방식 자체를 없앴다.
- 재현: 키 파일 경로를 비공개 경로 목록에 넣은 launch(PC에서 `init`한 상태)가 시작 전에 "Atlas credentials/config overrides…"로 거절되는 시험을 먼저 썼고, 수정 전에 실패하는 것을 확인했다. 실제 홈(키 파일 있음)에서 Codex 읽기 전용 `sci_reviewer` 프로필이 수정 전 0.3초 만에 거절되던 것이 수정 후 `labhq_annot pdb` 호출까지 성공했다(`gpt-5.6-luna`, 34초).
- 바꾼 것: `labhq/adapters/base.py`, `tests/test_atlas_key_file_1225.py`(신규, 24건), 인벤토리 한 줄. 기존 `tests/test_annot_alphagenome_atlas.py`의 override 거절 시험은 그대로 통과한다.
- 시험: 키 파일 경로가 있어도 launch 통과, 설정된 경로는 어떤 표기든 변수가 아님, labhq가 그 경로로 만드는 Claude 규칙 전부 통과, 그 밖의 모든 언급(변수 설정·목록·참조, `Env:\`·`Env:/`, `[Environment]::SetEnvironmentVariable`, 키 파일이 아닌 다른 경로에 든 이름)은 거절, 변수 이름이 키 파일 경로 옆에 있어도 거절, 키 값이 있으면 거절. 관련 시험 2581건 통과.
- manual: Atlas 절에 이 판정을 적는 일은 manual이 비는 대로 #1225 rev3에서 한다(manual은 지금 #1224가 쓰고 있다).
- 미검증: Claude·Gemini 엔진으로는 실제 실행하지 않았다(점검은 모든 엔진이 거치는 공통 코드이고 Claude 규칙은 생성 함수 출력으로 시험했다).

- rev3: 리뷰가 찾은 두 틈을 고쳤다. (1) CI에서 `claude_deny_private`가 운영자 `.labhq` 경로에 `RuntimeError`(테스트 가드)를 내 기존 Atlas 시험 15건이 실패했다 → 규칙 생성 실패는 그 규칙만 예외에서 빼고(더 엄격), 가드에 걸리지 않게 절대 경로로 만든다. (2) `annot.alphagenome_key_file`이 파일 이름만이면 그 이름이 예외 목록에 들어가 실제 변수 이름까지 지웠다 → 예외 형태는 구분자가 있는 절대 경로(POSIX·드라이브·UNC)나 `~` 형태만 두고, 이름만 있거나 상대 경로인 설정은 설정 기준 절대 경로로 풀어 쓴다. 정의: 예외는 설정된 키 파일의 anchored 경로와 labhq가 그 절대 경로로 만든 규칙뿐이다.
- rev4: CI(3.10·3.12)에서 기존 Atlas 시험 15건이 `RuntimeError: pytest refused operator .labhq path`로 실패했다. `RuntimeError`는 `policy.claude_deny_private`뿐 아니라 `Settings.path(raw)`(`base.py`)에서도 났다(테스트 가드가 CI의 기본 키 경로를 거절). 그 호출의 `except`에 `RuntimeError`를 더해 그 형태만 예외에서 빼고(더 엄격), 시험 1건을 더했다. 이 시험은 수정을 빼면 실패한다. 로컬에서는 conftest가 홈을 바꿔 가드가 걸리지 않아 재현하지 못했고, 시험은 `Settings.path`가 `RuntimeError`를 내게 해서 확인한다.
