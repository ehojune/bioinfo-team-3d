**KRAS G12C 저해제 연구 검토 보고서**

제공된 고정 snapshot만 대조했다. 아래 대응 관계는 snapshot에 명시된 정보이며, 외부 데이터베이스에서 독립적으로 검증한 결과는 아니다.

**연구 질문:** 6OIM 구조는 AMG 510의 KRAS G12C 결합 양상을 어떻게 설명하며, sotorasib·adagrasib·olomorasib의 활성 비교에는 어떤 공통 assay 조건이 필요한가?

| 대상 | 식별자 | snapshot에 명시된 내용 |
|---|---|---|
| 단백질–저해제 복합체 | PDB `6OIM` | KRAS G12C–AMG 510, 해상도 **1.65 Å** |
| 복합체의 ligand | `MOV` | 6OIM의 ligand 코드 |
| sotorasib | `CHEMBL4535757` | 화합물 accession |
| adagrasib | `CHEMBL4594350` | 화합물 accession |
| olomorasib | `CHEMBL6068410` | 화합물 accession |
| KRAS | `CHEMBL2189121` | 표적 accession |

근거는 사용자 제공 snapshot이다. 여기에 기재된 구조 출처는 [RCSB PDB 6OIM](https://www.rcsb.org/structure/6OIM)이며, 해당 페이지는 이번 검사에서 조회하지 않았다.

검사 가능한 수치와 교차 확인 결과는 다음과 같다.

- 식별자는 **PDB 1개, ligand 코드 1개, ChEMBL 4개**다. ChEMBL은 화합물 **3개**, 표적 **1개**로 구분된다.
- ChEMBL 문자열 **4/4개**가 `CHEMBL` 뒤 숫자 7자리 형태이며, 중복은 **0개**다. 이는 문자열 검사이며 accession의 실재 여부나 물질 동일성을 증명하지 않는다.
- `6OIM ↔ AMG 510 ↔ MOV` 연결은 snapshot에 명시돼 있다. 반면 **AMG 510/MOV ↔ sotorasib의 ChEMBL ID** 연결은 명시돼 있지 않아, 데이터베이스 간 물질 동일성은 이 자료만으로 확정할 수 없다.
- `CHEMBL2189121`은 KRAS 표적으로 제시됐다. 이 ID만으로 개별 assay가 **G12C 변이**를 사용했다고 판단할 수 없다.

**IC50 순위표는 작성하지 않았다.** 제공된 IC50 값은 **0개**, 조건을 대조할 수 있는 assay 기록도 **0개**다. 향후 값이 확보되면 생화학·세포 assay 구분, 표적 변이와 실험계, 노출·사전 반응 시간, 측정 지표, 농도 단위를 확인하고 조건별로 제시해야 한다. 단위 환산만으로 서로 다른 assay 결과를 한 순위표에 합쳐서는 안 된다.

검증 범위는 snapshot의 식별자·역할·명시된 연결 관계까지다. 원자 좌표, 화합물 구조, accession 원문 및 assay 기록이 없어 **결합 양상 분석, 데이터베이스 간 동일성 확정, 약효 우열 판단은 미검증**으로 남는다.