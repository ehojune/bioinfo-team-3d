## 2026-10-06 · #435 C ③ — labhq_annot 조절 영역·발현·AlphaGenome (#450)

- 결론: 직원이 ChIP-Atlas(enrichment·target genes), ENCODE cCRE, GTEx(발현·eQTL)를 내장 도구로 조회하고, 키가 있으면 AlphaGenome 예측도 부른다. 판본 기록·조회 로그·결과 파일·캐시는 #449와 같은 틀이다. 통제 구역 데이터의 변이·영역도 경고·승인 없이 조회한다(PI 결정 10-06).
- AlphaGenome 키: `labhq init`이 키가 없을 때 한 번 묻고(Enter면 건너뜀) `annot.alphagenome_key_file`에만 둔다. POSIX 0600, Windows는 상속을 끊고 현재 계정만. 설정·로그·이벤트·doctor·MCP 명령줄에는 값이 없고, doctor는 키 있음/없음만 보인다. 키나 `alphagenome` client가 없으면 도구를 등록하지 않는다.
- 바뀐 것: `labhq/tools/annot_regulatory.py`·`annot_keys.py`(새), `annot_mcp.py`, `annot.py`(pace 항목), `settings.py`(`annot`), `init_wizard.py`, `doctor.py`, `private_paths.py`(키 파일), 직원 4명 안내, CSO roster, 연결된 도구 표, `public_resources.tsv`, 예시 설정, manual. 기존 test 기대값 한 곳(`test_annot.py` stdio 도구 목록)은 새 도구가 그대로 바꾸는 값이라 고쳤다.
- 실행한 것: `tests/test_annot_regulatory.py` 23 passed·1 skipped(numpy 없음), 관련 test 15개 파일 752 passed·14 skipped, 전체 suite 4052 passed·55 skipped·1 failed(Windows). 실패한 `test_semantics_shadow_remove`의 한 test는 단독 3회 통과했고 첫 전체 실행에서도 통과해 부하 때문으로 본다. GTEx·ChIP-Atlas target genes·ENCODE는 새 코드로 실제 API를 불러 확인했다. `scripts/check_public.sh` 통과.
- 미해결: ChIP-Atlas enrichment는 제출까지만 실측했다. 10-06 18:01·18:06에 낸 두 잡이 18:59까지 결과를 내지 않아 응답 열은 EA 스크립트 소스를 따랐다. AlphaGenome은 키가 없어 실제 호출하지 않았다(client v0.9.0 소스 계약을 따름). Codex 로컬 리뷰는 30분 동안 진행 없이 멈춰 취소했다.
- 근거: `labhq/tools/annot_regulatory.py`, `tests/test_annot_regulatory.py`, PR #450 본문의 "근거 문서" 절.
