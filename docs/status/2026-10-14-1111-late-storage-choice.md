## 결론

저장 위치 질문 없이 끝난 요청도 API·CLI·웹에서 기존 카드를 열어 GitHub 정리본을 남깁니다. Refs #558.

## 바뀐 것

- `POST /api/requests/{rid}/storage`, `labhq storage <id>`, 검토 대기 카드의 **저장 위치 정하기**. 이미 선택·게시한 요청은 거부하고 열린 카드는 재사용합니다.
- 검토 전 선택은 검토 완료와 묶음 갱신을 기다립니다. 검토 후 선택은 바로 편집자 → 게시 승인 → 업로드로 이어집니다.
- README 문서 링크, 진행 기록의 수정 회차, 재현 방법의 환경·명령·입력 출처, 참고 자료의 DOI·PMID·문헌을 보강했습니다.
- 3D 검토 완료에 CP4 revision·hash를 전달합니다. 기존 CSS 검사도 현재 로드 순서 세 파일을 모두 확인합니다.

## 확인

- 기준 main `4a7c03b`: 새 Python 회귀 10 failed, 웹 회귀 2 failed. 관련 Python 35 passed, CP4 포함 43 passed.
- Node 48개 파일 모두 통과. GitHub 생성·업로드는 mock transport로만 검증했습니다.
- Windows 전체 pytest는 다른 레인의 실행이 이어져 15분 대기 후 미실행했습니다. main 충돌로 CI run이 생성되지 않았습니다. changes, pytest (3.10·3.12), pytest-windows, clean-install, ro-crate-validator, pytest-slow, readme, patch-notes 모두 미실행입니다.

## 남은 지적

- main 충돌 해소와 로컬 전체 pytest·CI 최종 검증은 총괄에게 넘깁니다.

- 실제 저장소 생성·게시, PI/trial 접속·재시작, 설정 변경, 리뷰 요청·인라인 답글·병합은 하지 않았습니다.

🤖 Generated with Codex (gpt-6) for the labhq dev lead
