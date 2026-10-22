## #678 일부 — doctor 저장공간 경고에 gateway 상태 드라이브 포함 (source 384db0c, base e41cfe2)

- 이전 동작: doctor `disk` 행은 작업·임시·Codex/Claude 직원 홈 드라이브만 읽었다. 그래서 작업 폴더를 D:로 옮겨도 gateway SQLite·이벤트(`gateway.state_dir`)가 여유 없는 C:에 있으면 경고가 없었다. 10-16 사례의 gateway 정지 위험이다.
- 현재 동작: gateway 상태 폴더의 볼륨이 이미 보인 행들의 볼륨과 다르면, 같은 읽기 전용 기준(10 GiB·5%)으로 `gateway state disk` 행을 더한다.
  - 볼륨은 `disk_space.usage`와 같은 방식(가장 가까운 기존 부모)으로 판정하며 폴더를 만들지 않는다.
  - 같은 볼륨이면 행을 반복하지 않는다. 볼륨을 구분할 수 없으면 행을 남긴다. 경로와 조회 예외 문구는 노출하지 않는다.
- 검사:
  - `tests/test_disk_preflight.py`에 3건을 더했다: 다른 볼륨의 부족 경고, 같은 볼륨 미반복, 없는 폴더의 부모 판정과 구분 불가 시 행 유지. 기존 단언은 그대로다.
  - 로컬 venv에 `websockets`가 없어 doctor를 import할 수 없었다. 그래서 같은 세 시나리오를 AST로 추출한 순수 실행으로 확인했다. 새 코드는 3건 통과, origin/main 코드는 다른 볼륨·구분 불가 2건에서 실패해 누락이 재현됐다.
  - 3.10 AST, profile, public, diff 검사 통과. 실제 pytest 결과는 CI에서 확인한다.
- 남은 #678 범위: task 시작·큰 다운로드 전 중단과 PI 카드, fail 단계 기준, 쓰기 가능한 큰 원자료 위치와 직원 브리핑. 이 issue 전체를 해결한 것이 아니며 시운전의 새 선결로 삼지 않는다.
