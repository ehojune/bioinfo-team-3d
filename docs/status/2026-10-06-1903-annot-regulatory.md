## 2026-10-06 · #435 C ③ — labhq_annot 조절 영역·발현·AlphaGenome (#450)

- 결론: 직원이 ChIP-Atlas(enrichment·target genes), ENCODE cCRE, GTEx(발현·eQTL)를 내장 도구로 조회하고, 키가 있으면 AlphaGenome 예측도 부른다. 판본 기록·조회 로그·결과 파일·캐시는 #449와 같은 틀이다. 통제 구역 데이터의 변이·영역도 경고·승인 없이 조회한다(PI 결정 10-06).
- AlphaGenome 키: `labhq init`이 키가 없을 때 한 번 묻고(Enter면 건너뜀) `annot.alphagenome_key_file`에만 둔다. POSIX 0600, Windows는 상속을 끊고 현재 계정만. 설정·로그·이벤트·doctor·MCP 명령줄에는 값이 없고, doctor는 키 있음/없음만 보인다. 키나 `alphagenome` client가 없으면 도구를 등록하지 않는다.
- 바뀐 것: `labhq/tools/annot_regulatory.py`·`annot_keys.py`(새), `annot_mcp.py`, `annot.py`(pace 항목), `settings.py`(`annot`), `init_wizard.py`, `doctor.py`, `private_paths.py`(키 파일), 직원 4명 안내, CSO roster, 연결된 도구 표, `public_resources.tsv`, 예시 설정, manual. 기존 test 기대값 한 곳(`test_annot.py` stdio 도구 목록)은 새 도구가 그대로 바꾸는 값이라 고쳤다.
- 실행한 것: `tests/test_annot_regulatory.py` 23 passed·1 skipped(numpy 없음), 관련 test 15개 파일 752 passed·14 skipped, 전체 suite 4052 passed·55 skipped·1 failed(Windows). 실패한 `test_semantics_shadow_remove`의 한 test는 단독 3회 통과했고 첫 전체 실행에서도 통과해 부하 때문으로 본다. GTEx·ChIP-Atlas target genes·ENCODE는 새 코드로 실제 API를 불러 확인했다. `scripts/check_public.sh` 통과.
- 미해결: ChIP-Atlas enrichment는 제출까지만 실측했다. 10-06 18:01·18:06에 낸 두 잡이 18:59까지 결과를 내지 않아 응답 열은 EA 스크립트 소스를 따랐다. AlphaGenome은 키가 없어 실제 호출하지 않았다(client v0.9.0 소스 계약을 따름). Codex 로컬 리뷰는 30분 동안 진행 없이 멈춰 취소했다.
- 근거: `labhq/tools/annot_regulatory.py`, `tests/test_annot_regulatory.py`, PR #450 본문의 "근거 문서" 절.

### 리뷰 반영 (19:18, 2a1ba2d)

- 고친 것: AlphaGenome 구간 요약을 요청 구간의 bin으로 자름(P1, 전에는 1 Mb 창 전체의 최대·평균), client create 30초 timeout·전용 thread 2개·실패 뒤 남은 항목 즉시 실패, 기본 길이 100KB와 1 bp 출력 전 track 500KB·1MB 거부, GTEx 여러 조직은 조직마다 받고 개수도 조직별, ChIP-Atlas request_id를 제출 때 입력·옵션 hash와 묶어 다르면 거부(기록 없는 id는 캐시 안 함), 유전자 철자 그대로 dedup·캐시, 키 폴더가 다른 항목·홈·labhq state를 품으면 저장 거부, runner 계정용 키 절차(`docs/runner-account.md`), `test_annot.py` stdio test를 임시 홈·설정으로 띄우고 도구 목록을 정확히 비교.
- 고치지 않은 것: AlphaGenome `score_variant`(권장 scorer) 전환은 출력 형식이 바뀌는 별도 작업이라 이번에는 크기 상한으로 막았다.
- GTEx 배열 파라미터(`tissueSiteDetailId` 반복)는 이 PC의 TLS 가로채기로 실측하지 못해, 이미 확인한 단일 조직 query를 조직마다 부르는 쪽을 골랐다.
