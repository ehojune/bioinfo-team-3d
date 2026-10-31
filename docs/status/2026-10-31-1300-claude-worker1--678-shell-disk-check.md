# #678 셸 다운로드·압축 해제 직전 디스크 확인

- 결론: Claude 직원의 Bash 명령이 정책에서 `allow`일 때 `approval_mcp`가 `disk_space.shell_check`를 불러, 대상 드라이브 여유가 `runner.disk_fail_free_gb`(2 GiB) 미만이거나 알려진 크기를 풀면 그 아래로 내려가면 `ask`(기존 tool_permission 카드)로 바꾼다. 크기를 모르는 다운로드는 막지 않고, 여유가 경고 기준(10 GiB·5 %) 아래면 `agent.log` warn만 남긴다. deny는 만들지 않는다.
- 크기 추정: `unzip`은 zip 중앙 디렉터리의 비압축 합, `tar`·`7z x`는 압축 크기 × 4(`ARCHIVE_RATIO`), `curl`·`wget`·`fasterq-dump`·`prefetch`·`aws`·`gsutil`·`rsync`·`git clone`은 미상. 대상 폴더는 `-o/-O/-P`·`-d`·`-C`·`-o<dir>`이 있으면 그것, 없으면 작업 폴더. 파이프·체인은 명령별로 읽고 가장 엄격한 결과를 쓴다. 해석하지 못한 것과 읽을 수 없는 드라이브는 건드리지 않는다.
- 바꾼 것: `labhq/disk_space.py`(`shell_check`), `labhq/tools/approval_mcp.py`(`_disk_decision`), `tests/test_disk_shell_check_678.py`, `config/ci/windows-boundary-v1.json`.
- 시험: 판정표·크기 추정·대상 폴더·체인·읽기 불가·`approval_prompt` 카드/허용/경고 17건 통과, 관련 기존 203건 통과.
- 적용 범위 밖: Codex·Gemini·cli 직원(엔진 sandbox가 명령을 직접 실행), 로컬 job 제출 경로(`daemon.py`는 이 claim 밖), PowerShell `Invoke-WebRequest`·`Expand-Archive`, 명령 도중 디스크가 차는 경우. 작업 도중 디스크 감시는 다음 PR.
- 미검증: 실제 디스크 부족 상황, 압축 배수 4의 실제 비율, Windows CI.
