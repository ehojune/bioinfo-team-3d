연구 요청의 저장 위치를 CP1 전에 고르고, 편집자가 만든 로컬 정리본을 웹에서 확인한 뒤 GitHub에 게시합니다. 확인 질문이 있으면 저장 위치를 같은 카드에 넣습니다.

## 변경
- 로컬 기본 / 새 저장소(공개·비공개) / 기존 저장소. 프로젝트 지정·재개 시 중복 질문 없음.
- editor 정규직과 README·진행 기록·재현 방법·출처·그림·표 정리본.
- 기존 redaction 재사용. 통제 입력 요청은 로컬 유지. 웹 검토 후 게시 또는 자동 게시 설정.
- 두 웹 화면·편집자 3D 표시·매뉴얼. Notion은 다음 단계.

## 검증
- Windows 전체: **5,951 passed, 66 skipped** (11분 16초). 모의 요청부터 가짜 GitHub 게시까지 포함.
- 관련 회귀: 163 passed, 1 skipped. 웹 Node 42개 파일 모두 실행.
- 신규 테스트는 main 기준 모듈 부재로 실패했고 구현 후 10 passed.
- 공개 검사·문서 생성 검사·patch-notes·notes-index·diff 검사 통과.

## CI와 인계
현재 PR은 main과 충돌(DIRTY)합니다. 현재 head의 CI는 생성되지 않았으며 이전 head의 결과를 통과 근거로 쓰지 않습니다.

| job | 현재 head 결과 |
|---|---|
| changes | 미생성 |
| patch-notes | 미생성 |
| readme | 미생성 |
| pytest (3.10) | 미생성 |
| pytest (3.12) | 미생성 |
| pytest-windows | 미생성 |
| clean-install | 미생성 |
| ro-crate-validator | 미생성 |
| pytest-slow | 미생성 |

## 남은 지적
- 지시서의 역할 분담에 따라 main 충돌 해소와 그 뒤 CI 확인은 총괄에게 넘깁니다. 완료 조건 중 현재 head CI는 미충족입니다.
- 그림 안의 개인정보는 PI가 검토합니다. 원본 입력·미보고 산출은 게시 대상에서 제외합니다.
- 실제 GitHub 저장소 생성·게시, PI/trial 실행, Notion, 리뷰 요청·지적 대응·병합은 수행하지 않았습니다.

Refs #558

🤖 Generated with Codex (gpt-6) for the labhq dev lead
