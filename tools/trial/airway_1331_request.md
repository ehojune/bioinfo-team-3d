공개 RNA-seq 데이터 airway(GEO GSE52778)를 원시 FASTQ부터 다시 분석해 줘. 기도 평활근 세포주 4개에 dexamethasone 1 µM 18시간 처리 여부를 비교한 실험이야(Himes et al. 2014, PLoS One).

## 데이터
- SRA run 8개, paired-end. 세포주 4개 × 처리/무처리:
  | run | 세포주 | 처리 |
  |---|---|---|
  | SRR1039508 | N61311 | untrt |
  | SRR1039509 | N61311 | trt |
  | SRR1039512 | N052611 | untrt |
  | SRR1039513 | N052611 | trt |
  | SRR1039516 | N080611 | untrt |
  | SRR1039517 | N080611 | trt |
  | SRR1039520 | N061011 | untrt |
  | SRR1039521 | N061011 | trt |
- FASTQ는 ENA에서 받고 받은 파일의 md5를 ENA 값과 대조해.

## 계산 환경
- 계산은 전부 연구소 HPC에서 해. 사이트 프로필에 지정된 노드 1개와 scratch 작업 폴더 안에서만 일하고, 그 밖의 노드·경로는 쓰지 마.
- 서버에 LLM을 설치하지 마. conda 환경, salmon, R/Bioconductor 같은 분석 도구 설치는 괜찮아.
- HPC 로그인은 기관 기록에 남아. 여러 단계가 같은 HPC 세션 하나를 함께 쓰고, 어느 60분 구간에서도 로그인이 2회를 넘지 않게 해. 접속이 끊기면 원인을 적은 뒤에만 다시 접속해.
- 보고서와 산출물에는 호스트·계정·노드 이름·원격 경로를 적지 마. 잡 번호와 실행 시간은 적어도 돼.

## 단계 (CSO가 계획할 때 지켜 줘)
1. **데이터 받기**와 **참조 index 만들기**는 서로 의존하지 않으니 다른 직원에게 맡겨 **동시에** 돌려.
   - 데이터 받기: 8 run의 FASTQ를 scratch에 받고 md5 대조표를 남긴다.
   - 참조 index: GENCODE human 최신 release의 transcriptome FASTA와 GTF를 받아 salmon index를 만든다(decoy는 선택). transcript→gene 표(tx2gene)도 만든다.
   - 두 단계 모두 무거운 작업은 클러스터 잡으로 제출해.
2. **정량**: 두 단계가 끝난 뒤 salmon quant를 샘플별 클러스터 잡으로 돌린다. mapping rate 표를 남긴다.
3. **통계**: tximport로 gene 수준으로 모으고 DESeq2로 `~ cell + dex`를 적합한다. 대조는 **trt 대 untrt**(log2FoldChange 양수 = dexamethasone에서 증가)로 한다.
   - 산출: `de_results.csv` — 열 `gene_id`(Ensembl, version 없이), `symbol`, `baseMean`, `log2FoldChange`, `lfcSE`, `pvalue`, `padj`. 모든 유전자를 한 표에.
   - MA plot, 샘플 PCA 그림.
4. **리뷰**: 다른 직원이 정량·통계를 점검한다. 특히 대조 방향, 세포주 공변량, mapping rate 이상치.
5. **보고서**: 방법(도구 버전 포함), 제출한 클러스터 잡 목록(잡 번호·단계·소요 시간), 주요 결과, 논문(Himes 2014)이 보고한 dexamethasone 반응 유전자(예: DUSP1, KLF15, PER1, TSC22D3, ZBTB16, FKBP5)와의 일치 여부, 한계.

결과 판정은 우리가 따로 스크립트로 하니, `de_results.csv` 열 이름과 대조 방향은 위 그대로 지켜 줘.
