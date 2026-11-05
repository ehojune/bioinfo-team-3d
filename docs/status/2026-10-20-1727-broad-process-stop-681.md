PR #766 · Refs #681 · P1 수정 `2b6b8b4`.

- 4322864519: EncodedCommand·e/ec·약어의 literal UTF-16LE Base64를 실행 없이 decode해 검사합니다. host 옵션·script 인수를 구분하고 decode 크기·재귀 상한과 불명확한 encoded 입력 거부를 유지합니다.
- 4322864528: 이름 선택 객체의 ForEach-Object/foreach/% MemberName·positional Kill·wildcard 호출을 거부합니다. common parameter operand와 명시 PID·조회·데이터는 구분합니다.
- 4322864535: stdbuf의 short/long option operand 뒤 literal command를 기존 검사에 연결합니다. 4318107934는 기존 공식 계약의 stale 근거 답글로 제외합니다([근거·회귀](https://github.com/ehojune/bioinfo-team-3d/blob/codex/broad-process-stop-681/docs/reference/process-stop-681-followup.md)).
- 검증: 원래 head에서 새 회귀 **37 failed, 25 passed**, 수정 뒤 guard 9개 파일·text encoding **649 passed**. policy·flat/nested fake broker의 deny·card/event/submit 미호출·pending 없음을 확인했습니다. 기존 test·assertion은 유지했습니다.
- Python 3.10 AST·integrations·public·patch-note·기록 원본·diff 검사 PASS. guard 환경을 유지하고 전체 실패 로그를 보존했습니다. 초기 oversized test ID의 Windows 환경 변수 길이 오류는 짧은 ID로 고쳤으며 assertion은 그대로입니다.

한계: 실제 process/proc_tree·shell·PI·연구·HPC는 실행하지 않았습니다. OS 격리·PID tracker #675·P2는 별도 범위입니다. 새 CI·새 head 리뷰/P1=0·병합은 controller가 맡습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
