## 2026-10-05 · #57 ⑧ — 사무실 소품을 탭 입구로

- 결론: 사무실 칠판·HPC 랙·문을 누르거나 키보드로 고르면 작업판·HPC·메신저 탭이 열린다. 결정이 기다리면 칠판이 깜박이고(움직임 줄이기 설정이면 테두리 색만) 결정 탭으로 안내한다.
- 바뀐 것: `labhq/web/index.html`(소품의 `data-tab-link`·role·tabindex, `initTabShell`이 돌려주는 탭 전환 함수 사용, 좁은 화면에서는 Command Center로 스크롤, `renderApprovals`가 칠판 상태를 바꿈), 매뉴얼 2.5D 절.
- 실행한 것: 새 `tests/web_office_props.cjs`를 CI 목록에 넣었다. 데모에서 랙→HPC, 문→메신저, 결정 2건 대기 중 칠판 깜박임→결정, Enter 키를 확인했다. web test 48 passed, `scripts/check_public.sh`.
- 미해결: 배정 때 서류 애니메이션은 넣지 않았다.
- 근거: `labhq/web/index.html`, `tests/web_office_props.cjs`.
