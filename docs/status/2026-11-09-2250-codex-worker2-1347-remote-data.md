# #1347 r3 — HPC 단계 연결·동시 실행

- 요청별 scratch 안의 잡별 `work/` 산출을 그대로 보존하고 다음 단계에 경로를 전달합니다. 이름·크기로 완료를 판정하며 새 hash·복사·권한 변경은 없습니다.
- whole-HPC도 `max_parallel_steps` 안에서 독립 단계를 동시에 보냅니다. 요청 폴더 생성 경합을 고쳤습니다. CP1 종류 문장은 paper·재배정·이어 가기 안내보다 먼저 나옵니다.
- 리뷰 P1: 선언 산출만 수집합니다. 선언 디렉터리는 실제 목록에 있어야 하며, 이름이 비슷한 하위 파일로 필수 파일 누락을 숨기지 않습니다.
- 검증: Windows 관련 회귀 334 passed·21 skipped. Linux helper smoke: 1,201개 파일·5 GiB sparse 산출의 위치/권한/수정시각 보존·재시도·크기 변경/링크 거절 PASS. mock 병렬 루트·의존 실패 전파·출력 분리·로그인 1회 PASS.
- 실제 HPC·다중 직원 운영은 UNVERIFIED. 원격 Linux `python3`가 필요하며 자동 삭제하지 않습니다. 최종 CI·실제 리뷰·병합 근거는 [PR #1348](https://github.com/ehojune/bioinfo-team-3d/pull/1348)에 기록합니다.
