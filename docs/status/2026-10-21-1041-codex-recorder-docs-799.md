Refs #799, #739

최신 main c8a33a8을 정상 merge해 #793 CI 정책과 #821/#822 변경을 보존했습니다. Recorder 소스는 기존 검토본 그대로이며 실제 bench는 실행하지 않았습니다.

- HANDOFF의 닫힌 PR 큐·Paper2Skill 병합 전 표기를 정리했습니다. 매뉴얼은 Linux 전수·Windows 기본 경계/선택 범위 검증 실패 시 전수·nightly/수동 전수를 구분합니다. README 한영판·C3 정책/CI 소스·생성 목차는 main과 같습니다.
- 집중 fake/coroutine/static 29건 PASS(로컬 1.98초). 성공/실패 spawn·누락과 0·arm 격리·재채점 receipt/sidecar 보존을 확인했습니다. Python 3.10 AST/compile 6파일과 변경 runtime 2파일 정답 누출 정적 검사 PASS.
- LF 문자 수: HANDOFF 3349→3333(-16), manual 175613→175667(+54); README 11686, 영문 17627로 변동 없습니다. main에 반영된 Paper2Skill 설명은 유지했습니다.
- 기존 git subprocess fixture 차단은 재실행하지 않았습니다. 실제 process/thread·PI·연구·bench·HPC·원자료·network fixture 미실행이며 속도·효용과 이번 head의 Linux/Windows CI는 미검증입니다. #799는 OPEN입니다.
- Public·diff·patch-notes·notes 결과는 로컬 검증 산출에 기록합니다. Push·PR 생성·CI 조회·리뷰 호출·병합·배포 없이 총괄에게 로컬 head를 넘깁니다.

- 총괄 독립 정적 검토에서 코드 P1=0을 확인했습니다. 참고 계약의 기록기 미구현 표기를 opt-in 구현/실측 미실행으로 바로잡았습니다.
- 동일 main Git 본문/LF 기준 README 한영·manual 합계 204926→204981자입니다. 실제 bench·효용은 미검증입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
