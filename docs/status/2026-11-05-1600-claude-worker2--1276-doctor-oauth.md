## 단계
#1276 rev1: doctor가 갱신할 수 없게 만료된 직원 Claude 로그인을 ok로 보고하던 문제.

## 한 일
- `labhq/doctor.py`: `claude_session_expired`를 더했다. 직원 폴더(`CLAUDE_CONFIG_DIR`, 없으면 `~/.claude`)의 `.credentials.json`에서 날짜만 읽는다. `refreshTokenExpiresAt`이 지났거나, 갱신 토큰 없이 `expiresAt`이 지났으면 만료로 본다. 이때 status가 0이어도 login 행을 fail로 바꾸고 로그인 증거를 blocked로 둔다.
- 쓰는 값은 날짜뿐이고 토큰은 읽거나 출력하지 않는다. 호출은 하지 않는다.
- 한계(04장 한·영에 적음): 서버에서 취소된 토큰, OS 키체인에 든 로그인은 못 본다.
- `tests/test_doctor_claude_oauth_1276.py` 13건: 날짜 조합, 읽을 수 없는 파일, doctor 행.

## 확인
- 새 시험 13건과 `-k doctor` 268건 통과.

## 하지 않은 것
- 이 PC의 실제 로그인 만료는 재현하지 않았다. 현재 직원 폴더의 갱신 토큰은 만료 27일 전이라 새 코드로는 ok다.
