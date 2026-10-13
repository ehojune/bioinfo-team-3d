## 결론

완료 요청을 현재 instance에서 키워드로 검색한다. `labhq recall "유전자"`와 client token으로 인증한 `GET /api/knowledge/search`는 최대 5건·본문 합계 2,000자를 돌려준다.

## 바뀐 것

- gateway 전용 SQLite 색인과 terminal 트랜잭션의 재처리 표식. 단일 worker가 checkpoint부터 25건씩 backfill한다.
- 한국어 3자 이상 FTS5 trigram, 2자·미지원 환경은 최근 색인 2,000건의 literal LIKE 검색.
- 쓰기·검색 시 restricted marker, private reference·비밀값 가림. 현재 정책으로 재검사하고 판정 불가 자료는 제외한다.
- 요청 요약·step 상태·오류 분류·출처만 사용한다. 원본 로그·산출 파일·reference_original을 읽지 않는다.
- doctor에 색인 수·마지막 성공·fallback을 표시하고 매뉴얼에 사용법을 추가했다.

## 확인

- 기준 main `f6d4fdf`: 새 회귀 **11 failed**. branch: **11 passed**, 라운드 기록 포함 focused **39 passed**.
- Windows 전체 pytest: **6,082 passed, 66 skipped** (719.26초).
- 공개 검사·diff 검사 통과. PR #574 패치노트·상태 원본 검사는 기록 커밋에서 수행한다.

## 남은 지적

- prompt 자동 주입·reflection·registry는 후속 범위다. 자료 전체를 읽거나 실패 원인을 새로 추론하지 않으며 오류 메타데이터가 없으면 `unknown`으로 남긴다.
- PI/trial 실행·설정·배포, 리뷰 요청·인라인 답글·병합은 하지 않았다.

Refs #60

🤖 Generated with Codex (gpt-6) for the labhq dev lead
