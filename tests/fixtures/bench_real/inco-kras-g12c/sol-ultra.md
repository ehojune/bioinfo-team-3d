# KRAS G12C 저해제 고정 참고 자료 검증 보고서

## 연구 질문

PDB `6OIM`의 구조 정보와 고정된 ChEMBL accession으로 KRAS G12C 저해제 3종 및 KRAS 표적을 일관되게 식별할 수 있는가? 또한 현재 자료로 약물 효능을 비교할 수 있는가?

## 식별자 교차 확인

| 구분 | 대상 | 식별자·수치 | 교차 확인 결과 |
|---|---|---|---|
| PDB 구조 | KRAS G12C–AMG 510 복합체 | `6OIM` | snapshot과 일치 |
| 구조 내 리간드 | AMG 510 리간드 코드 | `MOV` | snapshot과 일치 |
| 구조 해상도 | PDB `6OIM` | **1.65 Å** | snapshot과 일치 |
| 화합물 | sotorasib(AMG 510) | `CHEMBL4535757` | snapshot과 일치 |
| 화합물 | adagrasib | `CHEMBL4594350` | snapshot과 일치 |
| 화합물 | olomorasib | `CHEMBL6068410` | snapshot과 일치 |
| 표적 | KRAS | `CHEMBL2189121` | snapshot과 일치 |

`MOV`는 PDB의 리간드 component code이고 `CHEMBL4535757`은 ChEMBL 화합물 accession이므로, 두 문자열이 다른 것은 불일치가 아니다. 교차 연결은 **`6OIM`–`MOV`–AMG 510/sotorasib–`CHEMBL4535757`**로 정리된다.

## 정량 검사 결과

- PDB 구조: **1건**
- 구조 해상도: **1.65 Å**
- PDB 리간드 코드: **1개**
- ChEMBL accession: **4개**
  - 화합물: **3개**
  - 표적: **1개**
- 중복 accession: **0개**
- snapshot 내부 매핑 불일치: **0건**
- 제공된 IC₅₀ 값·단위·assay 조건: 각각 **0건**

## IC₅₀ 비교 및 검증 한계

IC₅₀ 순위표는 작성하지 않았다. 서로 다른 assay의 IC₅₀는 단위가 같더라도 assay 유형, 생화학·세포 기반 여부, 단백질 구성, 반응 시간, 기질 농도, 측정법 등이 다르면 직접적인 효능 순위를 나타내지 않는다.

현재 snapshot에는 IC₅₀ 수치와 assay 조건이 없으므로 다음은 검증할 수 없다.

- 세 저해제의 상대적 효능 또는 우열
- 생화학적 활성과 세포 활성의 일치 여부
- 동일 조건에서의 재현성이나 통계적 차이
- `6OIM` 구조로부터 adagrasib·olomorasib의 결합 방식이나 효능을 추론하는 것

검증 범위는 제공된 고정 snapshot의 문자열과 수치에 한정된다. RCSB 및 ChEMBL의 현재 레코드를 실시간 조회하지 않았으므로 accession의 최신 상태, 동의어 관계, 개별 activity record까지 독립 검증한 결과는 아니다.

**결론:** 고정 자료 내부에서 PDB·ChEMBL 식별자 간 모순은 발견되지 않았다. 다만 이 결과는 식별자 확인이며, 약효 비교나 IC₅₀ 순위화를 뒷받침하지 않는다.

근거: [RCSB PDB 6OIM](https://www.rcsb.org/structure/6OIM) 및 제공된 ChEMBL accession snapshot.