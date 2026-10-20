PR #766, 고침: `c484256`. Refs #681.

- 지정 P1 10건 중 9건을 수정했습니다. coproc·eval·backquote·timeout/nice/setsid·find action·dash·trap·pgrep→xargs kill·ANSI-C literal quote를 검사합니다. 재귀 상한 초과도 거부합니다.
- 4318107934는 원문의 positional Name 전제가 공식 5.1·7.5 계약과 7.5.3 소스에 맞지 않아 stale 근거를 답글로 남깁니다. 명시한 Name이 switch 뒤에 오는 경우는 현재도 deny합니다. [지적별 근거](../reference/process-stop-681-followup.md).
- 현재 head `d4317ec`의 mock baseline에서 **57 failed**를 재현했고, 수정 후 기존 회귀 포함 **368 passed**입니다. policy와 flat/nested fake broker가 카드·event·submit·pending 없이 deny하며 PID·데이터·조회 음성 사례를 유지합니다. guard 환경 3개 유지·blocked 0건, 전체 실패명·traceback 로컬 보존.
- Python 3.10 AST·integrations·public·diff·patch-note·notes 검사 PASS. 기록은 저장소 검사에 맞춰 기존 평면 entry에 추가했습니다. 현재 main과 충돌이 없습니다.
- 실제 shell/process/proc_tree·PI·연구·HPC는 미검증입니다. 동적 명령 격리·PID tracker #675와 기존 P2는 범위 밖이며 CI·fresh-head 리뷰·병합·배포는 supervisor 담당입니다.
