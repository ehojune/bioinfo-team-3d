Fixes #665
PR #791: 조상 입력 보호·성공 retry 증거·부분 정리·입력 hash 계약을 유지하고 CI fixture만 보정했습니다.

- 현재 head CI의 Linux 3.10/3.12는 같은 두 테스트에서 실패했습니다: `test_deleted_output_without_a_recorded_hash_is_missing`, `test_codex_profiles_keep_same_scoped_read_grants[False-workspace-write]`. 실행 중 run의 `--log-failed`는 제공되지 않아 완료된 job 로그를 API로 보존했습니다.
- `RunContext` stand-in에 실제 기본값 `read_only_inputs=[]`를 명시하고, 작은 hash fixture의 output/input cap을 함께 고정했습니다. missing/unrecorded·exit code·scoped read/deny assertion은 그대로입니다. production에 누락 필드를 허용하는 분기는 추가하지 않았습니다.
- 같은 두 실패를 mock으로 재현한 뒤 관련 **31 passed**, 최신 main 통합 뒤 같은 **31 passed**. 새 hash 허용·상한 거절·partial cleanup·native profile 구성·retry identity 회귀를 함께 확인했습니다. 전체 traceback은 작업별 D: artifact에 보존했고 기존 guard 환경을 유지했습니다.
- main `b9abcec7`의 #801 문서·#790 종료 기록 변경을 보존해 합쳤습니다. HANDOFF 충돌은 main 우선순위 표와 #791 검증 기록을 함께 남겨 해결했습니다(LF 4,748→3,439자). manual에는 #791 병합 전 구현임을 표시합니다.
- public·diff·Python 3.10 AST·patch-notes·notes 검사 통과. 실제 process/thread/signal/proc_tree·직원 CLI·PI·원자료/HPC·연구/bench·설치·게시·배포는 실행하지 않았으며 #790의 process/signal 테스트도 로컬에서 돌리지 않았습니다. native/전체 Windows 검증·최종 head CI·새 리뷰/P1=0·병합은 controller 담당입니다. 기존 SyntaxWarning·P2는 범위 밖입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>