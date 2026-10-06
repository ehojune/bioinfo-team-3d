# topic별 근거 논문

topic 43개마다 분석 방법·표준·벤치마크를 뒷받침하는 논문 목록이다. 찾아볼 때만 읽는다.

- 범위: PI 결정(2026-10-07, #446)에 따라 지금은 topic마다 10편 이상, 나중에 30편까지 늘린다. 한 논문이 여러 topic의 근거가 될 수 있어 topic별 수를 더하면 전체 편수보다 크다.
- 전체 1255편, 그중 429편이 두 topic 이상에 걸린다. topic별 최소 20편, 최대 82편, 10편 미만 topic 없음. 30편 미만은 6개(`viral_genomics` 20, `metaproteomics` 23, `microarray_expression` 24, `amplicon_sequencing` 25, `metagenome_assembly` 28, `metatranscriptomics` 29).
- 만든 방법: topic마다 문헌 검색으로 후보를 모으고 같은 PMID는 한 행으로 합쳐 topic을 이었다.
- 검증: 2026-10-07에 모든 PMID를 NCBI esummary로 조회해 제목 단어가 절반 이상 겹치는지 확인했다(LLM 없음). 1255편 모두 통과했고 빠진 행은 없다.
- 파일: [topic_papers.tsv](topic_papers.tsv) — `pmid, first_author, year, journal, kind, topics, title, why`. 첫 topic 순, 같은 topic 안에서는 최신 연도 순이다.

## topic별 편수

| topic | 편수 |
|---|---:|
| `alternative_splicing` | 30 |
| `amplicon_sequencing` | 25 |
| `atac_seq` | 55 |
| `bacterial_genome_assembly` | 31 |
| `bulk_rna_seq` | 42 |
| `chip_seq` | 38 |
| `cut_and_run` | 35 |
| `de_novo_genome_assembly` | 58 |
| `dna_methylation` | 51 |
| `genome_annotation` | 47 |
| `germline_wgs_wes` | 62 |
| `gwas` | 44 |
| `hi_c` | 54 |
| `immune_repertoire_sequencing` | 36 |
| `long_read_transcriptomics` | 30 |
| `metabolomics` | 54 |
| `metagenome_assembly` | 28 |
| `metagenome_binning` | 33 |
| `metagenomic_functional_profiling` | 30 |
| `metagenomic_taxonomy` | 39 |
| `metaproteomics` | 23 |
| `metatranscriptomics` | 29 |
| `microarray_expression` | 24 |
| `multi_omics_integration` | 34 |
| `ont_long_read` | 58 |
| `pacbio_long_read` | 62 |
| `pangenomics` | 31 |
| `perturb_seq` | 34 |
| `phylogenetics` | 30 |
| `proteomics` | 60 |
| `proteomics_dia` | 64 |
| `rare_disease_genomics` | 49 |
| `single_cell_multiome` | 46 |
| `single_cell_proteomics` | 33 |
| `single_cell_rna_seq` | 53 |
| `somatic_wgs_wes` | 50 |
| `spatial_epigenomics` | 37 |
| `spatial_metabolomics` | 43 |
| `spatial_multiomics` | 44 |
| `spatial_proteomics` | 73 |
| `spatial_transcriptomics` | 82 |
| `structural_variant_calling` | 56 |
| `viral_genomics` | 20 |

## 종류별 편수

| kind | 편수 |
|---|---:|
| method | 655 |
| review | 179 |
| benchmark | 155 |
| landmark | 146 |
| protocol | 65 |
| guideline | 55 |
