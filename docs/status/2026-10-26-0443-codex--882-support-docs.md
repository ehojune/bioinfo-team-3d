Refs #882. HANDOFF 현재 큐와 manual의 기존 중단 조건 절을 최신 main·PR과 대조했습니다.

- #967 소유권 P1 해소·port P2 보류, #937/#968 취소 복구 구현·회귀와 #966 OPEN(착수 시 `4ea349bc`) → #965 저장 후속을 구분했습니다. #920은 이미 병합된 것으로 표시했습니다.
- 실제 transport·gateway OS 재시작·새 설치의 실제 요청·반복 복구는 운영 미검증입니다. 기존 중단 결정·별도 GO를 유지하며 #882는 닫지 않습니다.
- HANDOFF 3,550→3,996자, manual 192,774→193,672자: 합계 +1,344자. 상세 근거는 기존 reference에 연결했습니다.
- 변경 링크 25개 경로·anchor/URL 형식, GitHub 대상·상태 대조, public·diff·patch-notes·notes 검사를 통과했습니다. 문서 변경으로 로컬 runtime test는 실행하지 않았습니다.
- 리뷰 후 #760 최신 댓글 확인 의무를 복원하고 역할 기준은 #882 최신 배정 댓글을 직접 연결했습니다(`e322c96`).
- 코드·설정·설치·배포·연구·HPC 변경/실행은 없습니다. 최종 head CI·동일 head 봇 P1=0 확인 뒤 병합합니다.

🤖 Co-Authored-By: Codex (gpt-6) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
