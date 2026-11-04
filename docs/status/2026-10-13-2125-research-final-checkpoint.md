## 결론

opt-in 연구 요청의 기존 **검토 완료**를 CP4 수용으로 연결합니다. 별도 승인 카드는 만들지 않습니다.

## 바뀐 것

- 생성 시 설정·계약 version 고정. 리뷰 accept·보고서 검사 뒤 초안 revision과 본문·감사·claim hash를 저장합니다.
- 유효한 PI receipt 전에는 `review_pending`. 거부·만료·취소·hash 변경은 완료하지 않습니다. 재시작은 저장 초안을 재사용합니다.
- 웹·CLI 결정에 revision/hash를 묶고 bundle·verify에 초안과 receipt를 연결합니다. 수용은 게시·전송·HPC 권한이 아닙니다.
- 기준 `1b41209`. #565·#556·#567·#571 포함. 총괄의 순서 해제 지시에 따라 착수했습니다.

## 확인

- 새 회귀: 기준 main에서 1 failed → branch 통과. 관련 Python 330 passed·4 skipped, 최종 집중 56 passed. Node 43개 파일 통과.
- Windows 전체 pytest: **UNVERIFIED**. 다른 레인의 전체 실행이 연속으로 이어져 동시 전체 실행 금지를 지켰습니다. CI 결과와 구분합니다.
- 공개·diff 검사 통과. 패치노트·상태 문서를 기록하고 patch-notes·notes-index를 검사했습니다.
- 매뉴얼 122,610 → 123,414자.

## 남은 지적

- 레인 임시 폴더 삭제가 자동 승인 검토의 `blocked by policy`로 차단돼 정리를 인계합니다.
- 로컬 Windows 전체 통과 조건은 미충족입니다. PI/trial live 검증·배포·CP3·CP4 수정 재실행은 범위 밖입니다.
- 봇 리뷰 요청·대응·병합은 총괄에게 넘깁니다.

Refs #90

🤖 Generated with Codex (gpt-6) for the labhq dev lead
