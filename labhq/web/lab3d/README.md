# 종이숲 연구소 / Paperwood Lab

P4 선행 제안. **Draft · 병합 보류**. PI가 선택한 원본 ART.md를 옮긴 아트 랩이다.
종이·나무 모형, 약 2등신, 낮은 책상과 원본 12석 배치를 유지한다. 문어·거북은 종의 비율을 따른다.
종이 #F1EDE3 · 크림 #FFF2D8 · 나무 #CB9C6E · 세이지 #718C73 · 먹색 #354D47 · 승인 빨강 #C25445.

| 직원 | 형태 · 업무 소품 |
|---|---|
| cso | 부엉이: 귀깃·얼굴 원반 / 질문 분기 카드 |
| chief_of_staff | 펭귄: 긴 몸·날개·납작한 발 / 브리핑 폴더 |
| biologist | 곰: 큰 귀·어깨·주둥이 / 생물학 도감 |
| bioinfo-agent | 수달: 긴 허리·가는 꼬리 / 파이프라인 큐브 |
| data_steward | 다람쥐: 치켜든 말린 꼬리 / 체크섬 클립보드 |
| lit_scout | 여우: 삼각 귀·뾰족한 뺨·큰 꼬리 / 돋보기 |
| analyst | 너구리: 눈가 마스크·줄무늬 꼬리 / 산점도 |
| engineer | 문어: 물방울 머리·여덟 팔 / 렌치 |
| qc_reviewer | 고슴도치: 가시·뾰족한 코 / QC 도장 |
| sci_reviewer | 거북: 등딱지·작은 머리 / 세 탭 리뷰 문서 |
| recruiter | 비버: 앞니·격자 꼬리 / 오퍼레터 |
| contract | 병아리: 둥근 몸·종이모자 / 논문의 방법 |

- 실행: 저장소 루트에서 `python -m http.server 8765 --bind 127.0.0.1 --directory labhq/web`, 브라우저에서 `http://127.0.0.1:8765/lab3d/`를 연다. 종료는 Ctrl+C.
- 확인: 직원 탭·상태 선택·회전·줌·80px 도감·윤곽. 폰(≤480px)은 선택/승인 대기 이름표만 표시한다. 승인 칩은 대기 직원을 순회하며 확대한다.
- 데모: `?skin=cso:placeholder`, `?contracts=3`, `?contracts=4&skin=c_b:placeholder`. 기본 12명은 모두 procedural이다.
- 스킨 계약: `await buildSkin(ctx, entry) → {root: Object3D, anchors:{head,handL,handR,label}, budget:{triangles,drawCalls,shared}, setState(state), update(dt,t,options), dispose()}`.
- `ctx={shapes,parent,def,index}`; 시간은 초, `options={reduced,silhouette}`. root는 좌석/도감 변환을 받고 앵커는 그 자손이다. procedural drawCalls는 공유 배치 수이며 직원별로 더하지 않는다.
- 스킨 선택은 [src/skins.js](src/skins.js) 한 곳. 여섯 상태는 queued·working·waiting·hibernating·done·error. 표지는 공용 head 레이어이며 스킨과 함께 해제한다.
- 로컬 관찰 API: `window.__labhq3d`의 `characters`, `setState`, `getState`, `stats`, `snapshot`. 외부 연결은 없고 `/3d` 게이트웨이 서빙은 별도 PR이다.
- 예산: 공유 형상·재질 InstancedMesh, CanvasTexture 3장, 그림자맵 0개, DPR ≤2. 탭 숨김/동작 줄이기에서는 반복 렌더를 멈춘다.
- 에셋 절차·예산·라이선스는 [SKINS.md](SKINS.md). iPhone Safari 실기 성능·발열·종 식별률·실제 CLI/HPC 연동은 **UNVERIFIED**.
