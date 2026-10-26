# 로컬 계산 작업판 표시 — Refs #675

- 변경: 기존 job event·pending_jobs를 요청/단계에 연결하고 공통 단계 흐름·task card·3D 요청 줄에 로컬 계산 이름·관측 상태를 표시합니다. 로컬 제출은 프로세스 시작 후 event라는 runner 계약에 맞춰 실행 중으로 표시합니다. HPC와 구분하며 진행률은 모름으로 둡니다.
- 정리: 종료 job·완료/실패/취소 요청·다음 턴·재접속 snapshot에서 이전 대기 표시를 제거합니다. event 밖의 job은 ID·상태 확인 중만 표시합니다.
- 검증: 신규 mock 13건 포함 관련 Node 44건, Python Node wrapper·CI 수집 검사 13건 PASS. 기존 reducer golden 43 event와 snapshot 복구 검사 PASS. 수정 전 신규 fixture는 실패했습니다. 2.5D 요청 cache의 job-only 갱신과 같은 scheduler ID의 task별 격리도 검사합니다.
- 문서: manual 로컬 job 절 1,427 → 1,171자. 기존 설명을 교체했습니다.
- 한계: 실제 browser·운영 요청·연구 QA·WSL·실제 계산 진행률은 미검증입니다. backend·설치·배포·HPC 실행은 없습니다. #675는 닫지 않습니다.
- 병합 gate: 필수 CI·알려진 head P1·충돌·최종 head 확인은 PR에서 기록합니다. 배정에 따른 PI 임시 별도 리뷰 대기 생략을 적용합니다.
