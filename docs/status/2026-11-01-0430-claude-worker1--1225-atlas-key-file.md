# #1225 — AlphaGenome 키 파일이 있는 PC에서 annot 직원 실행이 막힘

- 결론: Atlas 시작 점검이 "launch 설정 문자열에 환경 변수 이름 `ALPHAGENOME_API_KEY`의 글자가 있으면 거절"이었는데, `labhq init`이 두는 키 파일 `~/.labhq/secrets/alphagenome_api_key`의 경로가 `labhq_annot` 서버의 `LABHQ_PRIVATE_PATHS`와 Claude 설정의 `permissions` 거부 목록에 들어가서 키 값이 없는데도 거절됐다. 점검을 **이름이 가리키는 변수**와 **키 값**으로 바꿨다.
- 판정(`labhq/adapters/base.py`의 `names_atlas_key`): launch 설정(엔진 인자·prefix, MCP 서버 환경, Claude 설정)을 구조째 훑어, 변수 이름이 변수로 쓰인 경우만 거절한다. 변수로 설정함(`env`의 키, `shell_environment_policy.set.<이름>=`), 목록에 올림(`env_vars=["<이름>"]`), 참조함(`${이름}`·`$이름`·`%이름%`)이 해당한다. 경로 한 구성요소로 들어간 같은 글자는 앞에 경로 구분자나 단어 문자가 있어 걸리지 않는다. 키 값이 설정에 들어 있으면(`atlas_credentials`) 이름과 상관없이 거절하는 것은 그대로다.
- 재현: 키 파일 경로를 비공개 경로 목록에 넣은 launch(PC에서 `init`한 상태)가 시작 전에 "Atlas credentials/config overrides…"로 거절되는 시험을 먼저 썼고, 수정 전에 실패하는 것을 확인했다. 실제 홈(키 파일 있음)에서 Codex 읽기 전용 `sci_reviewer` 프로필이 수정 전 0.3초 만에 거절되던 것이 수정 후 `labhq_annot pdb` 호출까지 성공했다(`gpt-5.6-luna`, 29초, 입력 94,275·출력 699 토큰).
- 바꾼 것: `labhq/adapters/base.py`, `tests/test_atlas_key_file_1225.py`(신규, 16건), 인벤토리 한 줄. 기존 `tests/test_annot_alphagenome_atlas.py`의 override 거절 시험은 그대로 통과한다.
- 시험: 키 파일 경로가 있어도 launch가 통과, 경로는 변수가 아님(5가지 표기), 변수를 설정·목록·참조하면 이름 판정이 참(8가지), 변수 이름이 키 파일 경로 옆에 있으면 여전히 거절, 키 값이 있으면 여전히 거절. 관련 70개 파일 2570건 통과.
- 미검증: Claude·Gemini 엔진으로는 실제 실행하지 않았다(점검은 모든 엔진이 거치는 공통 코드라 같은 판정이다). 이 점검이 들어온 시점은 확인하지 않았다.
