# 스킨 넣기

1. 모델과 라이선스를 확인하고 `assets/<이름>/`에 저장한다. 외부 URL은 허용하지 않는다.
2. glTF 2.0(`.gltf` + 로컬 buffer/texture) 또는 `.glb`로 내보낸다. Draco/KTX2 등 별도 디코더가 필요한 압축은 현재 지원하지 않는다.
3. Y-up, +Z 정면, 발바닥 중앙이 원점. 몸체 높이 약 2 scene unit에 맞춘다. 앉은 캐릭터는 `offset:[0,0.67,0]`부터 조정한다.
4. `src/skins.js`의 해당 SKINS 항목을 아래 형태로 바꾼다. 다른 직원은 공유 procedural 배치에 남는다.
```js
{"id":"cso","type":"gltf","src":"./assets/my-model/model.glb","scale":1,"offset":[0,0.67,0],"rotationY":0,
 "clips":{"queued":"Idle","working":"Work","waiting":"RaiseHand","hibernating":"Sleep","done":"Clap","error":"Oops"}}
```
5. `scale`은 균일 배율, `offset`은 좌석 기준 이동, `rotationY`는 라디안이다. 카메라·사무실 좌표는 건드리지 않는다.
6. 선택 데모를 추가하려면 같은 항목을 MODEL_SKINS에 등록하고 `?skin=cso:<모델 id>`를 쓴다. 여러 대상은 `?skin=cso:placeholder&skin=analyst:placeholder`.
7. 6상태·도감·폰 화면·라이선스·파일 크기를 확인하고 아래 pytest와 공개 검사를 돌린다.

| 항목 | 규칙 |
|---|---|
| 앵커 | `anchor_head`, `anchor_hand_l`, `anchor_hand_r`; 없으면 변환 후 바운딩박스로 추정 |
| 손 움직임 | 손 앵커를 손/팔 메시 또는 뼈의 부모로 두면 클립 없는 상태에서도 손 올리기 적용 |
| 표지·이름표 | head에 공용 승인 깃발·zZ·체크·땀방울. label은 좌석 앞 공용 이름표 위치 |
| 표지 크기 | 상태 표지는 스킨 scale과 무관하게 같은 크기로 보인다. |
| 애니메이션 | clips는 상태→클립 이름. 매핑 누락은 같은 상태 이름을 찾고, 클립이 없으면 몸 흔들기·기울기·손 포즈로 대체 |
| 해제 | 공용 표지 dispose 후 스킨 dispose. glTF mixer·geometry·material·texture·skeleton을 해제 |
| 모델 예산 | 권장 ≤5,000 triangles · ≤4 draw calls/명 · 파일당 ≤2MB. 기본 사무실은 한 자릿수 draw calls 목표 |
| 파일 상한 | `check_public.sh`는 5MB 초과를 막는다. 내장 placeholder는 50KB 이하 |
| 측정 | `__labhq3d.stats()`는 실제 장면 calls/triangles/fps. 스킨 budget은 자원 추정치이며 숨은 면·다중 패스와 다를 수 있음 |

- 파견직: `?contracts=2..4`는 `c_a`–`c_d`를 만든다. 첫 좌석은 원래 위치, 추가 좌석은 입구 쪽이다. A–D 모자 배지는 색과 글자로 구분한다.
- 라이선스: 모델 폴더의 `LICENSE`와 이 문서에 출처·제작자·라이선스·변경 내용을 적는다. CC0도 출처를 남기고, CC-BY는 제작자·원문 링크·수정 사실을 화면 크레딧에도 표시한다.
- 생성형 3D는 서비스 약관의 상업 사용·재배포·귀속 조건을 확인한다. 확인하지 못한 항목은 **UNVERIFIED**로 기록하고 공개 에셋에 넣기 전에 해결한다.
- 내장 `assets/placeholder.gltf`: 이 저장소의 `generate_placeholder.py`로 직접 생성, CC0-1.0, 외부 모델·텍스처 없음. working 클립 1개로 클립 재생과 나머지 상태의 대체 동작을 함께 확인한다.
- 재생성: `python labhq/web/lab3d/assets/generate_placeholder.py`. 확인: `.venv\Scripts\python -m pytest -q tests/test_lab3d.py`, `bash scripts/check_public.sh`.
