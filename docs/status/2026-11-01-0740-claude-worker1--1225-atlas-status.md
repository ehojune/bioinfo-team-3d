# #1225 — manual의 AlphaGenome Atlas 절을 현재 main에 맞춤

- 결론: 절 제목의 "미병합·hold"를 "2026-10-31 병합"으로 고치고, PI가 키를 넣는 법을 더했다. 절의 나머지 문장은 `labhq/runner/annot_broker.py`·`labhq/tools/annot_alphagenome_atlas.py`·`labhq/doctor.py`와 `docs/reference/alphagenome-atlas-621.md`에 대조했고 어긋난 곳은 제목뿐이었다(입력 1–10개, scorer 기본값 `RNA_SEQ`, 읽기 전용·Atlas deny 거부, 시작 점검).
- 더한 것(manual 한·영): 켜는 법. 키는 runner 프로세스의 환경 변수 `ALPHAGENOME_API_KEY`만 쓰고 `labhq init`의 키 파일과 별개다. Windows 사용자 환경 변수로 넣는 PowerShell 세 줄(`Read-Host -AsSecureString`로 입력을 가리고 값을 출력하지 않음), `setx`처럼 명령줄에 키를 쓰지 말 것, 새 터미널에서 `labhq down`·`labhq up`, POSIX 방법, SDK 설치(`pip install -e ".[alphagenome]"`), `labhq doctor`의 `AlphaGenome Atlas` 행이 뜻하는 것, 쓰는 직원 범위(`chief_of_staff`·`sci_reviewer`는 받지 않음).
- 확인: PowerShell 세 줄의 동작(`SetEnvironmentVariable`로 사용자 변수 설정, 읽기, 삭제)을 Windows PowerShell 5.1에서 시험 값으로 확인하고 시험 변수는 지웠다. 실제 키로는 하지 않았다. `integrations.py --check` 통과.
- 바꾼 것: `docs/manual.md`, `docs/manual.en.md`. 코드·시험은 바꾸지 않았다.
- 미검증: 실제 Atlas 조회(키로 서비스를 부른 적 없음)는 계속 UNVERIFIED다. POSIX 한 줄은 실행하지 않았다. 새 터미널·재시작 뒤 runner가 키를 받는지는 실제 키로 확인하지 못했다.
