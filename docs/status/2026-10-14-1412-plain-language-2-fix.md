## 결론

#588의 남은 지적 6건을 수정했습니다. Refs #557.

## 바뀐 것

| 항목 | 수정 |
|---|---|
| 1 · P1 통제 데이터 | 요청 문장·경로·참고 자료를 policy로 판정하고 오류·불명확 시 모델 호출 차단 |
| 2 · P1 비용 | 통역 비용·timeout 미집계 비용을 실행 중 예산에 한 번만 반영하고 소진 시 호출 차단 |
| 3 · P2 한국어 | 영문 기술 용어를 허용하고 코드·경로는 거부 |
| 4 · P2 이어 묻기 | 2.5D·3D에서 번역된 오류도 실패 문구·스타일 유지 |
| 5 · P2 진행 | 내부 통역 task를 요청 단계·진행·snapshot의 running_tasks에서 제외 |
| 6 · P2 종료 | 진행 중 통역을 shutdown_readiness의 finishing에 포함 |

## 확인

- main `4476228`: 새 Python 회귀 77개 실패, Node 렌더링 회귀 2개 실패.
- 수정 후 관련 pytest 235 passed, Node 5개 파일 통과. 실제 모델·GitHub 호출은 mock만 사용했습니다.
- 공개 저장소·패치노트·기록 목차 검사 통과.
- 별도 clone venv 사용. 전체 pytest는 CI에 맡깁니다.

## 남은 지적

- CI·자동 봇 리뷰 확인은 총괄 인계. 브라우저 육안 확인은 UNVERIFIED.

🤖 Generated with Codex (gpt-6-astra) for the labhq dev lead
