PR #766 · Refs #681 · P1 수정 `45f35f0`.

- 4323699194: 이름 선택 괄호식·선택 변수 receiver의 Kill()을 거부합니다. 괄호식의 앞선 출력도 추적하며 명시 PID·인용된 변수 문자열·호출 없는 member 조회는 구분합니다.
- 4323699213: ionice의 class/classdata·ignore option 뒤 실행 command를 검사합니다. PID/PGID/UID 대상 지정·option operand·help/version은 실행형과 구분합니다.
- 4323699225: Bash locale quote의 비번역 literal을 double quote처럼 정규화합니다. 인접 fragment와 escaped/single-quoted 데이터도 구분합니다. 4318107934는 기존 stale 근거 답글로 제외합니다([근거·회귀](https://github.com/ehojune/bioinfo-team-3d/blob/codex/broad-process-stop-681/docs/reference/process-stop-681-followup.md)).
- 검증: 원래 head에서 **31 failed, 30 passed**, 수정 뒤 guard 10개 파일·text encoding **710 passed**. policy·flat/nested fake broker의 deny·card/event/submit 미호출·pending 없음을 확인했습니다. 기존 test·assertion과 guard 환경은 유지했습니다.
- Python 3.10 AST·integrations·public·patch-note·기록 원본·diff 검사 PASS. policy·runner·workflow·공통 fixture와 #800 반영을 보존했습니다. main과 충돌은 없었습니다.

한계: 실제 process/proc_tree·shell·번역 catalog·PI·연구·HPC는 실행하지 않았습니다. OS 격리·PID tracker #675·P2는 별도 범위입니다. 새 CI·새 head 리뷰/P1=0·병합은 controller가 맡습니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
