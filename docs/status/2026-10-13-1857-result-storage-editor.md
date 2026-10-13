연구 요청의 저장 위치를 CP1 전에 고르고, 편집자가 만든 로컬 정리본을 웹에서 확인한 뒤 GitHub에 게시합니다. 확인 질문이 있으면 저장 위치를 같은 카드에 넣습니다.

## 변경
- 로컬 기본 / 새 저장소(공개·비공개) / 기존 저장소, 요청·프로젝트 선택 저장.
- editor 정규직과 README·진행 기록·재현 방법·출처·그림·표 정리본.
- 기존 redaction 재사용, 통제 입력 요청은 로컬 유지. 게시 승인 또는 자동 게시 설정.
- 두 웹 화면의 선택·검토 카드와 매뉴얼. Notion은 다음 단계.

## 검증
- main 기준 신규 테스트: result_storage 모듈 부재로 실패. 구현 후 신규 10 passed(모의 요청 → 가짜 GitHub 포함).
- 최종 관련 회귀 163 passed, 1 skipped. 실제 node.exe로 웹 42개 파일을 모두 실행했습니다.
- 공개 검사·문서 생성 검사·diff 검사 통과.
- Windows 전체 pytest 재실행 중(Node 경로 교정·직원 수/스킨/웹 CI 등록 보완 후). CI는 현재 head로 확인해 갱신합니다.

## 남은 지적
- 실제 GitHub 저장소 생성·게시, PI/trial 실행, Notion은 수행하지 않았습니다.
- 그림 안의 개인정보는 PI가 검토합니다. 원본 입력·미보고 산출은 게시 대상에서 제외합니다.

Refs #558

🤖 Generated with Codex (gpt-6) for the labhq dev lead
