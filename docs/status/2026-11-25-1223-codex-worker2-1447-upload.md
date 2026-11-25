# HPC 셸 업로드 한도

Refs #1447

- 사이트 프로필의 `upload_max_bytes`는 파일당 양의 정수 바이트입니다. 생략하면 524,288(512 KiB), 10 MiB를 쓰려면 10,485,760을 적습니다.
- `hpc_submit`의 첨부 입력은 최대 20개입니다. 합계 한도는 4 MiB와 파일당 한도 중 큰 값이며, 남은 용량을 넘는 파일은 보내기 전에 거부합니다.
- 가짜 스케줄러에서 512 KiB와 10 MiB의 정확한 경계값, 1바이트 초과, CRLF, 설정을 낮춘 뒤 기존 잡의 입력 회수를 검증했습니다. 관련 pytest 278건 통과·38건 건너뜀, 공개 검사 통과입니다. 실제 HPC 및 배포는 미검증입니다.

🤖 Co-Authored-By: Codex (gpt-6.2-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
