## 결론

PR #587: 큰 연구 라운드도 사람용 본문을 GitHub issue로 게시하고 원본은 로컬 기록에 보존한다.

## 바뀐 것

- 한도를 넘으면 JSON을 빼고 로컬 `rounds/<rid>.json`을 안내한다.
- 그래도 길면 결과 보고서를 Markdown 줄 단위로 줄이고 로컬 전문 경로를 남긴다.
- 같은 marker 이슈는 수정하며 sha256 중복 방지를 유지한다.
- 과거 크기 초과 `failed` delivery는 재시작 때 다시 시도한다. 게시 기록이 있으면 건드리지 않는다.

## 확인

- 새 회귀 4건은 main에서 실패하고 branch에서 통과했다. 관련 pytest 42 passed.
- 최신 main 병합 뒤 focused 33 passed.
- Windows 전체 pytest: **6,278 passed, 66 skipped** (733.86초). Node CJS 48개 파일 통과.
- 공개·diff 검사 통과. `%TEMP%`의 작업용 pytest 폴더 삭제 완료.
- CI는 기록 커밋 push 뒤 확인한다.

## 남은 지적

- PI/trial 실행·설정·접속, 실제 라운드 저장소 쓰기, 리뷰 요청·인라인 답글·병합은 하지 않았다.

Refs #69

🤖 Generated with Codex (gpt-5.6-sol) for the labhq dev lead
