# 디스크 사전 점검 #752

현재 동작의 검증 근거만 보관한다.

| 범위 | 근거·한계 |
|---|---|
| doctor | `disk_space.usage`가 미생성 경로의 기존 부모를 조회한다. 작업·임시·Codex/Claude 홈을 각각 표시한다. 기본 10 GiB/5%, 하나라도 미달하거나 조회 실패면 warn. 디렉터리를 만들거나 이동하지 않는다. |
| init | 새 설정에서 경로를 묻고 Windows 시스템 드라이브가 128 GiB 미만이거나 경고 기준 미달이면 여유 있는 고정 데이터 볼륨 후보를 보인다. 기존 설정 보존·`--yes`에서는 자동 변경하지 않는다. |
| 게시 검사 | 절대/실제 workspace와 상대 경로의 하위 파일을 검사한다. `runs` 같은 일반 단어·URL은 제외한다. portfolio 2곳·result_storage 5곳에서 settings를 전달한다. 공개 게이트·내부 자료 기준은 유지한다. |
| 종료 표시 | 기존 WebSocket/runner 상태로 2.5D·3D 경고를 표시한다. 재연결 중 유지, 복구 시 해제. 종료와 네트워크 장애를 단정하지 않는다. 웹이 닫혀 있으면 외부 알림은 없다. |
| unit/mock | 신규·추가 관련 pytest 41 passed. 기존 관련 묶음 221 passed. Node 관련 3개 통과. 종료 알림·업로드는 가짜 WS·MockTransport로 검증한다. |
| 환경 실패 | 기존 패키징 test의 `-I` subprocess는 지정 venv에 없는 `markdown_it` 때문에 실패한다. 일반 test는 clone의 무시된 `.pr-drafts/test-deps`에 기존 의존성 markdown-it-py 4.2.0/mdurl 0.1.2를 설치해 실행했다. 공유 venv·test는 바꾸지 않았다. |
| 운영 검증 | 실제 인스턴스 재시작·종료·시험 알림·설정 변경·HPC·분석·배포는 미실행. 브라우저 시각 QA는 UNVERIFIED. |

문서 길이(LF 정규화 Unicode): `docs/manual.md` 161,060 → 161,719자(+659). 설치·디스크 절만 갱신했다.
