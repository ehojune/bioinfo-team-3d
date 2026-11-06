## 단계
#1300(#1295 A단계 나머지): 사이트 프로필 schema·검사기, 처음 설정 질문, 저장소 밖 저장 경로, 공개 검사.

## 한 일
- `labhq/hpc_site.py`(새 모듈): `SiteProfile` schema(모르는 키 거부, 접속 명령에 비밀번호·토큰 글자 거부, 이름·원격 경로 형식), `unknown_fields`(아직 모르는 결정)·`problems`(답끼리의 모순), 저장·읽기(`~/.labhq/sites/<이름>.yaml`, `LABHQ_SITES_DIR`로 바꿈, 저장소나 `.git` 폴더 안이면 거부, 임시 파일로 쓰고 바꿔치기, POSIX는 0600), 빈 예시 `example_text()`, 질문 흐름 `ask_profile`(빈 답은 "모름"으로 두고 추측하지 않음, 잘못된 답은 다시 물음).
- `labhq/init_wizard.py`: `labhq init`이 sge·pbs·slurm에서 프로필을 적을지 묻는다. 터미널에 사람이 없으면(`--yes`, 파이프) 묻지 않고, 설정에 프로필 이름이 있으면 다시 묻지 않는다. init의 scheduler·큐·로그인 상한이 기본값으로 들어간다.
- `labhq/settings.py`: `hpc.site_profile`(이름만, 경로 불가).
- `scripts/check_public.sh`: 줄 첫머리가 프로필 표식인 추적 파일이나 `.labhq/sites/` 경로가 있으면 실패.
- 기관 값은 넣지 않았다.

## 확인
- `tests/test_hpc_site_profile_1300.py` 50 passed(가짜 입력, 임시 폴더, 임시 git 저장소에서 공개 검사 실행).
- `-k "init or settings or hpc_login or site_profile or example_config"` 374 passed, 6 skipped. `check_public.sh`·`patch_notes.py check` 통과(Windows 로컬).

## 하지 않은 것
- 세션 관리자(#1299)가 프로필을 읽는 연결은 C단계다.
- 이미 만든 설정에 프로필을 묻는 흐름(`_ask_existing_hpc_login_limits` 짝)은 넣지 않았다. init 새 설정만 묻는다.
- manual 문장은 claim 밖이라 쓰지 않았다(총괄 지시로 #1295 rev7이 08·11장에 사이트 프로필 정책을 적는다).
- 실제 HPC 접속은 하지 않았다.
