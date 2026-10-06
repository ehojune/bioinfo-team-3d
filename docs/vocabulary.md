# 어휘·topic·점검표

labhq가 계획과 산출에 붙이는 이름표를 한곳에 모은 표입니다. 찾아볼 때만 읽습니다.
이 파일은 `scripts/vocab_tables.py --write`가 `labhq/vocab/`의 `output_types.yaml`·`topic_checklists.yaml`·`edam_subset.yaml`·`public_resources.tsv`에서 만듭니다. 손으로 고치지 말고 원본을 고친 뒤 다시 만드세요.

| 무엇 | 수 | 쓰임 |
|---|---:|---|
| topic(분야) | 43 | CSO가 계획마다 적는 분야 이름표. 점검표와 분야 규칙 pack을 고르는 열쇠 |
| 점검표 항목 | 60 | topic마다 계획이 답해야 하는 점검. 할 수 있으면 하고, 못 하면 이유를 밝힌다 |
| 데이터 종류(data) | 30 | 단계 산출에 붙이는 이름표 |
| 파일 형식(format) | 28 | 단계 산출에 붙이는 이름표 |
| 작업(operation) | 22 | 단계 산출에 붙이는 이름표 |
| 공개 자원 | 21 | 결과를 공개 DB와 잇는 참고 목록(목록 밖 자원도 쓴다) |

## topic

| topic | 정의 | 점검표 항목 | EDAM |
|---|---|---:|---|
| `bulk_rna_seq` | bulk RNA sequencing for gene or transcript expression analysis | 4 | RNA-Seq (topic_3170) |
| `single_cell_rna_seq` | single-cell or single-nucleus RNA sequencing with cell-resolved expression measurements | 3 | Single-cell sequencing (topic_4028) |
| `spatial_transcriptomics` | transcript measurements retained with positions in intact tissue | 3 | - |
| `atac_seq` | ATAC-seq analysis of chromatin accessibility | 3 | ATAC-seq (topic_4053) |
| `chip_seq` | ChIP-seq analysis of protein-DNA binding or histone marks | 3 | ChIP-seq (topic_3169) |
| `cut_and_run` | CUT&RUN or CUT&Tag profiling of chromatin-associated targets | 3 | - |
| `dna_methylation` | sequencing-based measurement of DNA methylation | 3 | - |
| `germline_wgs_wes` | germline variant analysis from whole-genome or whole-exome sequencing | 3 | - |
| `somatic_wgs_wes` | somatic variant analysis from tumor-normal or tumor-only whole-genome or whole-exome sequencing | 3 | - |
| `rare_disease_genomics` | rare-disease genome analysis using pedigree, phenotype or candidate-ranking context | 3 | Rare diseases (topic_3325) |
| `ont_long_read` | Oxford Nanopore long-read DNA or RNA sequencing | 2 | - |
| `pacbio_long_read` | Pacific Biosciences long-read sequencing, including HiFi and Iso-Seq data | 2 | - |
| `long_read_transcriptomics` | transcriptome analysis using long RNA or cDNA reads | 3 | - |
| `metagenome_assembly` | shotgun metagenome assembly, binning and metagenome-assembled genome analysis | 2 | Metagenomics (topic_3174) |
| `metagenomic_taxonomy` | taxonomic profiling of shotgun metagenomic reads without genome assembly as the primary goal | 3 | - |
| `amplicon_sequencing` | marker-gene amplicon sequencing for community composition or targeted taxonomy | 3 | - |
| `viral_genomics` | viral genome reconstruction, variant analysis or lineage assignment | 3 | Virology (topic_0781) |
| `bacterial_genome_assembly` | de novo assembly and annotation of a bacterial isolate genome | 3 | - |
| `alternative_splicing` | alternative splicing or isoform usage in transcriptomic data | 3 | RNA splicing (topic_3320) |
| `microarray_expression` | gene expression measured by microarray after platform-specific preprocessing | 5 | - |
| `proteomics` | mass-spectrometry protein identification, quantification and comparison | 0 | Proteomics (topic_0121) |
| `proteomics_dia` | data-independent-acquisition proteomics processing and quantification | 0 | - |
| `single_cell_proteomics` | protein identification and quantification at single-cell resolution | 0 | - |
| `spatial_proteomics` | spatially resolved protein measurement and tissue proteome analysis | 0 | - |
| `metaproteomics` | protein identification and quantification in microbial communities | 0 | Metaproteomics (topic_4060) |
| `metabolomics` | small-molecule measurement processing and comparative metabolite analysis | 0 | Metabolomics (topic_3172) |
| `spatial_metabolomics` | spatially resolved metabolite measurement and analysis | 0 | - |
| `gwas` | genome-wide association testing and downstream locus interpretation | 0 | GWAS study (topic_3517) |
| `structural_variant_calling` | detection and genotyping of large insertions, deletions and rearrangements | 0 | Structural variation (topic_3175) |
| `de_novo_genome_assembly` | reference-free assembly and polishing of non-bacterial genomes | 0 | Sequence assembly (topic_0196) |
| `genome_annotation` | gene and feature annotation of assembled genomes | 0 | - |
| `pangenomics` | multi-genome or graph-reference construction, genotyping and comparison | 0 | - |
| `phylogenetics` | sequence-based tree inference, placement and comparative evolution analysis | 0 | Phylogenetics (topic_3293) |
| `hi_c` | chromosome-conformation contact, loop, compartment and domain analysis | 0 | - |
| `spatial_epigenomics` | spatial profiling and analysis of epigenetic features | 0 | - |
| `multi_omics_integration` | joint statistical analysis of distinct omics layers or assays | 0 | Multiomics (topic_4021) |
| `single_cell_multiome` | joint single-cell analysis of RNA, chromatin, protein or other modalities measured in the same cells | 0 | - |
| `spatial_multiomics` | joint spatial analysis of two or more molecular modalities | 0 | - |
| `perturb_seq` | pooled perturbation screens with single-cell molecular readouts | 0 | - |
| `immune_repertoire_sequencing` | BCR, TCR or other adaptive immune-receptor repertoire analysis | 0 | Immunoinformatics (topic_3948) |
| `metagenomic_functional_profiling` | gene, pathway and functional profiling from metagenomic data | 0 | Metagenomics (topic_3174) |
| `metagenome_binning` | grouping metagenomic contigs into metagenome-assembled genomes | 0 | - |
| `metatranscriptomics` | RNA sequencing of microbial communities for activity and function | 0 | Metatranscriptomics (topic_3941) |

## 점검표

계획은 항목마다 `step:<단계>`(그 단계에서 함), `assumption: <이유>`(못 함, 이유와 함께), `not_applicable: <이유>`(해당 없음) 중 하나로 답합니다. 근거 문헌은 [topic 점검표 근거](reference/topic_checklists_sources.md)에 있습니다.

### bulk_rna_seq

| id | 점검 | 이유 |
|---|---|---|
| `batch` | 처리 batch(스캔 날짜, array·lane, 기관)와 비교 집단의 교락을 확인하고 모형에 넣거나 한계로 보고한다 | 기술 차이를 생물학적 차이로 해석하지 않기 위해서다 |
| `pairing` | 같은 환자·공여자 시료는 주 모형과 유전자 세트 검정 모두에서 짝을 유지하고 짝 없는 시료를 밝힌다 | 개인차를 조건 효과로 잘못 세지 않기 위해서다 |
| `gene_set_test` | 유전자 세트 검정은 주 모형의 통계량으로 순위를 매기거나 짝 보존 방식으로 하며 짝을 무시한 재검정을 주 결과로 쓰지 않는다 | 주 분석의 짝과 보정을 경로 검정에서도 보존하기 위해서다 |
| `independent_validation` | 독립 코호트·데이터셋이 있으면 핵심 결과를 검증하고 없으면 이유를 밝힌다 | 한 데이터셋에만 맞는 결론인지 확인하기 위해서다 |

### single_cell_rna_seq

| id | 점검 | 이유 |
|---|---|---|
| `pseudobulk` | 공여자 간 비교는 세포가 아니라 공여자 단위(pseudobulk 등)로 한다 | 세포를 독립 생물학적 반복으로 세는 오류를 막기 위해서다 |
| `qc` | 시료별 QC 기준과 doublet·ambient RNA 처리를 밝힌다 | 시료별 품질과 오염이 집단 차이처럼 보일 수 있기 때문이다 |
| `batch` | 통합(integration) 전후 batch 효과를 확인한다 | 통합이 생물학적 신호를 지우거나 기술 차이를 남길 수 있기 때문이다 |

### spatial_transcriptomics

| id | 점검 | 이유 |
|---|---|---|
| `spot_qc` | 스팟·세포별 QC 기준(검출 유전자 수, UMI 수, 조직 밖 스팟 제외)을 밝힌다 | 조직 밖이나 손상된 스팟이 공간 패턴처럼 보일 수 있기 때문이다 |
| `deconvolution_reference` | 스팟 단위 기술이면 세포 유형 분해에 쓴 참조 단일세포 데이터와 출처를 밝힌다 | 참조가 조직과 맞지 않으면 세포 구성 추정이 틀어진다 |
| `section_replicates` | 공간 변이·영역 비교는 공간 자기상관을 고려한 방법으로 하고, 절편·시료 단위 반복을 밝힌다 | 스팟을 독립 반복으로 세면 유의성이 부풀려진다 |

### atac_seq

| id | 점검 | 이유 |
|---|---|---|
| `library_qc` | TSS enrichment, FRiP, 단편 길이 분포(뉴클레오좀 주기), 미토콘드리아 read 비율을 보고한다 | 라이브러리 품질이 나쁘면 접근성 차이가 기술 잡음일 수 있다 |
| `blacklist` | blacklist 영역을 빼고 중복 read 처리 방식을 밝힌다 | 반복 서열·인공 신호가 peak로 잡히는 것을 막는다 |
| `differential_model` | 차등 접근성은 consensus peak 정의를 밝히고 생물학적 반복을 갖춘 count 모형으로 판정한다 | peak 겹침 비교만으로는 차이를 판정할 수 없다 |

### chip_seq

| id | 점검 | 이유 |
|---|---|---|
| `input_control` | input(또는 IgG) 대조를 쓰고 peak 호출 방법과 임계값을 밝힌다 | 배경 신호를 빼야 결합 위치를 구분할 수 있다 |
| `replicate_reproducibility` | 반복 간 재현성(IDR 등)으로 최종 peak를 정한다 | 한 반복에만 있는 peak는 잡음일 가능성이 크다 |
| `library_qc` | FRiP, 라이브러리 복잡도(NRF·PBC), 교차상관 지표(NSC·RSC)를 보고하고 blacklist를 뺀다 | 항체·라이브러리 품질이 결과 해석의 전제다 |

### cut_and_run

| id | 점검 | 이유 |
|---|---|---|
| `controls_normalization` | IgG 같은 대조와 spike-in 정규화 여부를 밝힌다 | 배경과 전체 신호량 변화를 구분하기 위해서다 |
| `peak_calling` | 배경이 낮은 자료에 맞는 peak 호출 방법(SEACR 등)과 임계값을 밝힌다 | ChIP용 기본값은 이 자료에서 거짓 peak를 늘리거나 놓칠 수 있다 |
| `replicates` | 조건당 생물학적 반복을 두고 반복 간 일치를 보고한다 | 단일 시료 결과는 일반화할 수 없다 |

### dna_methylation

| id | 점검 | 이유 |
|---|---|---|
| `conversion_rate` | bisulfite 변환 효율(비CpG 메틸화율 또는 spike-in)을 보고한다 | 변환이 불완전하면 메틸화가 과대추정된다 |
| `coverage_snp` | CpG별 최소 coverage 기준과 C>T SNP 위치 제외를 밝힌다 | 낮은 coverage와 SNP는 메틸화 수준을 왜곡한다 |
| `differential_model` | 차등 메틸화는 반복과 coverage를 반영한 모형(beta-binomial 등)으로 판정한다 | 비율만 비교하면 coverage 차이를 무시하게 된다 |

### germline_wgs_wes

| id | 점검 | 이유 |
|---|---|---|
| `pipeline_reference` | 정렬·중복 표지·변이 호출 과정(예를 들어 GATK best practices)과 참조 유전체 판본을 밝힌다 | 파이프라인과 참조 판본이 변이 목록을 바꾼다 |
| `sample_qc` | 시료 QC(평균 coverage, 성별 일치, 혈연·오염 점검)를 보고한다 | 시료 혼동과 오염을 분석 전에 잡기 위해서다 |
| `variant_filtering` | 변이 필터 기준(VQSR 또는 hard filter)과 Ti/Tv 같은 call set 지표를 보고한다 | 필터가 정밀도와 민감도를 함께 정하기 때문이다 |

### somatic_wgs_wes

| id | 점검 | 이유 |
|---|---|---|
| `matched_normal` | 짝지은 정상 시료로 생식계열 변이를 거르고, 없으면(tumor-only) gnomAD 같은 집단 생식계열 자료로 거른 뒤 남는 오분류 위험을 한계로 밝힌다 | panel of normals는 기술적 인공물만 걸러 그 환자 고유의 생식계열 변이를 체세포 변이로 잘못 부를 수 있다 |
| `purity_contamination` | 종양 순도와 교차 오염을 추정해 보고한다 | 순도와 오염이 VAF 해석과 검출력을 바꾼다 |
| `artifact_filters` | panel of normals, FFPE·산화 같은 인공 변이 필터, VAF·depth 기준을 밝힌다 | 시료 처리와 시퀀싱 인공물이 체세포 변이처럼 보인다 |

### rare_disease_genomics

| id | 점검 | 이유 |
|---|---|---|
| `phenotype_hpo` | 표현형을 HPO 용어로 정리해 후보 우선순위에 쓴다 | 표현형이 수많은 후보를 줄이는 주된 근거다 |
| `inheritance` | 가계와 유전 양식(de novo, 열성, X연관)을 고려하고, 있으면 trio 등 가족 자료를 쓴다 | 유전 양식이 맞지 않는 후보를 거르기 위해서다 |
| `acmg_classification` | 변이 해석은 ACMG/AMP 기준으로 근거와 함께 분류하고, 임상 확정에는 확인 검사가 필요함을 밝힌다 | 연구 분석 결과를 진단처럼 읽지 않게 하기 위해서다 |

### ont_long_read

| id | 점검 | 이유 |
|---|---|---|
| `basecalling` | basecaller와 모델 판본, read 품질·길이 분포(N50)를 보고한다 | basecaller 판본에 따라 정확도가 크게 다르다 |
| `error_profile` | homopolymer 등 오류 특성에 맞는 도구와 polishing을 쓰고 짧은 indel 해석에 주의한다 | ONT 고유 오류가 indel 변이처럼 보일 수 있다 |

### pacbio_long_read

| id | 점검 | 이유 |
|---|---|---|
| `read_type` | HiFi와 CLR을 구분하고 read 정확도·길이 분포를 보고한다 | read 종류에 따라 맞는 도구와 기대 정확도가 다르다 |
| `platform_tools` | 정렬·변이 호출에 PacBio 자료에 맞춘 도구·모델을 쓰고 판본을 밝힌다 | short-read용 기본값은 긴 read 오류 특성과 맞지 않는다 |

### long_read_transcriptomics

| id | 점검 | 이유 |
|---|---|---|
| `isoform_definition` | full-length read 선별과 isoform 인정 기준(지원 read 수, 5'·3' 완결성)을 밝힌다 | 잘린 read가 새 isoform처럼 보일 수 있다 |
| `novel_isoform_support` | 새 isoform은 참조 주석·short-read·독립 증거로 확인하거나 후보로 표시한다 | 단일 근거의 새 isoform은 인공물일 가능성이 있다 |
| `quantification` | isoform 정량 방법과 조건당 반복을 밝힌다 | 반복 없이 isoform 차이를 판정할 수 없다 |

### metagenome_assembly

| id | 점검 | 이유 |
|---|---|---|
| `bin_quality` | MAG의 완결성·오염(CheckM 등)과 품질 등급(MIMAG)을 보고한다 | 오염된 bin은 존재하지 않는 유전체를 만든다 |
| `host_removal` | 숙주 read 제거와 assembly 방식(시료별 또는 co-assembly)·파라미터를 밝힌다 | 숙주 서열과 assembly 선택이 회수되는 유전체를 바꾼다 |

### metagenomic_taxonomy

| id | 점검 | 이유 |
|---|---|---|
| `database_version` | 분류 데이터베이스와 판본을 밝힌다 | 분류 결과가 데이터베이스에 크게 좌우된다 |
| `negative_controls` | 추출·시약 blank 같은 음성 대조로 오염을 점검한다 | 저생물량 시료에서는 시약 오염이 결과를 지배할 수 있다 |
| `compositional_analysis` | 상대 풍부도는 조성 자료에 맞는 방법(CLR 변환 등)으로 비교하고 시퀀싱 깊이 차이를 처리한다 | 비율 자료를 그대로 비교하면 거짓 상관이 생긴다 |

### amplicon_sequencing

| id | 점검 | 이유 |
|---|---|---|
| `region_primers` | 표적 영역(예를 들어 16S V4)과 primer 제거 방식을 밝힌다 | 영역과 primer가 검출 가능한 분류군을 정한다 |
| `denoising` | ASV denoising(DADA2 등)과 chimera 제거 기준을 밝힌다 | 시퀀싱 오류와 chimera가 가짜 분류군을 만든다 |
| `controls` | 음성 대조와 mock community로 오염과 정확도를 점검한다 | 대조 없이는 오염과 실제 신호를 구분할 수 없다 |

### viral_genomics

| id | 점검 | 이유 |
|---|---|---|
| `consensus_masking` | consensus는 최소 depth 기준 아래 위치를 N으로 가리고 그 기준을 밝힌다 | 낮은 coverage 위치의 염기는 믿을 수 없다 |
| `primer_dropout` | amplicon 방식이면 primer를 제거하고 amplicon 탈락 구간을 확인한다 | primer 서열과 탈락 구간이 변이·결손처럼 보인다 |
| `lineage_versions` | lineage·clade 지정 도구와 데이터 판본(Pangolin, Nextclade 등)을 밝힌다 | 판본이 바뀌면 같은 서열의 lineage가 달라진다 |

### bacterial_genome_assembly

| id | 점검 | 이유 |
|---|---|---|
| `completeness_contamination` | 완결성·오염(CheckM, BUSCO)과 coverage를 보고한다 | 불완전하거나 오염된 assembly는 유전자 유무 판단을 틀리게 한다 |
| `species_identity` | 종 동정(ANI 등)으로 시료 정체를 확인한다 | 라벨이 틀린 균주를 다른 종으로 해석하지 않기 위해서다 |
| `annotation_versions` | 주석 도구와 데이터베이스 판본(Bakta·Prokka, 항생제 내성 DB 등)을 밝힌다 | 주석 판본에 따라 유전자·내성 판정이 달라진다 |

### alternative_splicing

| id | 점검 | 이유 |
|---|---|---|
| `replicates_junctions` | 조건당 생물학적 반복과 junction read 최소 기준을 밝힌다 | 반복과 junction 근거 없이 splicing 차이를 판정할 수 없다 |
| `depth_read_length` | read 길이와 depth가 splicing 정량에 충분한지 보고한다 | 짧은 read와 낮은 depth는 이벤트 추정을 불안정하게 만든다 |
| `event_support` | 주요 이벤트는 sashimi plot이나 독립 증거로 확인하거나 후보로 표시한다 | 단일 도구의 이벤트 호출은 인공물을 포함한다 |

### microarray_expression

| id | 점검 | 이유 |
|---|---|---|
| `batch` | 처리 batch(스캔 날짜, array·lane, 기관)와 비교 집단의 교락을 확인하고 모형에 넣거나 한계로 보고한다 | 기술 차이를 생물학적 차이로 해석하지 않기 위해서다 |
| `pairing` | 같은 환자·공여자 시료는 주 모형과 유전자 세트 검정 모두에서 짝을 유지하고 짝 없는 시료를 밝힌다 | 개인차를 조건 효과로 잘못 세지 않기 위해서다 |
| `gene_set_test` | 유전자 세트 검정은 주 모형의 통계량으로 순위를 매기거나 짝 보존 방식으로 하며 짝을 무시한 재검정을 주 결과로 쓰지 않는다 | 주 분석의 짝과 보정을 경로 검정에서도 보존하기 위해서다 |
| `independent_validation` | 독립 코호트·데이터셋이 있으면 핵심 결과를 검증하고 없으면 이유를 밝힌다 | 한 데이터셋에만 맞는 결론인지 확인하기 위해서다 |
| `probe_mapping` | probe→gene 집약 규칙과 플랫폼 주석 판본을 밝힌다 | 여러 probe와 오래된 주석이 유전자별 결과를 바꿀 수 있기 때문이다 |

## 데이터 종류(data)

| key | 정의 | EDAM |
|---|---|---|
| `raw_counts` | read or fragment counts per gene or transcript and sample before normalization or transformation | Gene expression matrix (data_3112) |
| `normalized_counts` | counts per gene or transcript and sample adjusted for library size or other normalization factors, on an untransformed count scale | Gene expression matrix (data_3112) |
| `normalized_expression` | expression values per gene or transcript and sample expressed in normalized units such as CPM or TPM, before log or variance-stabilizing transformation | Gene expression matrix (data_3112) |
| `transformed_expression` | expression values per gene or transcript and sample after a log or variance-stabilizing transformation | Gene expression matrix (data_3112) |
| `de_table` | differential expression results per gene or transcript and tested contrast, including an expression effect estimate and statistical significance results | - |
| `enrichment_table` | enrichment results per gene set, biological function or pathway and tested comparison, including method-specific enrichment measures, p-values and multiple-testing-adjusted significance where available | Over-representation data (data_3753) |
| `network` | nodes and edges between biological entities, such as interactions or pathways | Pathway or network (data_2600) |
| `sample_metadata` | one row per sample with its design variables and annotations | Sample annotation (data_3113) |
| `data_manifest` | a list of data files or accessions with where they came from and how they were checked | - |
| `qc_report` | a report of quality checks on data or results, with pass, warn or fail per check | Quality control report (data_3914) |
| `report` | a human-readable write-up of what was done, found and left open | Report (data_2048) |
| `sequence_reads` | unaligned reads as they come from a sequencer, with or without qualities | - |
| `sequence` | one or more biological sequences that are not raw reads | Sequence (data_2044) |
| `alignment` | sequences or reads placed against each other or against a reference | Sequence alignment (data_0863) |
| `variants` | positions where samples differ from a reference, with alleles and calls | Sequence variations (data_3498) |
| `assembly` | contigs or scaffolds built from reads | Sequence assembly (data_0925) |
| `genomic_features` | genomic intervals representing genes, transcripts, exons or called peaks | Sequence features (data_1255) |
| `variant_annotations` | annotations linked to variants, such as overlapping genes, predicted consequences or known clinical interpretations | - |
| `protein_structure` | three-dimensional coordinates of a protein or complex | Protein structure (data_1460) |
| `plot` | a figure made from data for people to look at | Plot (data_2884) |
| `table` | tabular data that fits none of the more specific data keys | - |
| `abundance_table` | a matrix of quantitative feature abundances across samples or observations, excluding gene or transcript expression matrices | Matrix (data_2082) |
| `cell_feature_matrix` | a matrix of molecular feature measurements for individual cells or spatially resolved cell-like units | Count matrix (data_3917) |
| `coverage_track` | a genome-position track of read depth, signal intensity or another continuous coverage measure | Annotation track (data_3002) |
| `methylation_profile` | methylation measurements at cytosines or genomic intervals with their positions and measured levels | - |
| `taxonomic_profile` | taxonomic assignments for a sample or features, optionally with counts or relative abundances | Taxonomic classification (data_1872) |
| `phylogenetic_tree` | a tree representing inferred evolutionary relationships among sequences, taxa or genome bins | Phylogenetic tree (data_0872) |
| `splicing_table` | per-event or per-isoform splicing measurements and, when tested, differential splicing statistics | - |
| `copy_number_profile` | copy-number estimates or segments across a genome for one or more samples | - |
| `spatial_annotations` | spatial coordinates, boundaries or assignments linking molecular observations to cells or tissue locations | - |

## 파일 형식(format)

| key | 정의 | 확장자 | EDAM |
|---|---|---|---|
| `fastq` | FASTQ text with sequence and quality lines | .fastq, .fq | FASTQ (format_1930) |
| `fasta` | FASTA text with header and sequence lines | .fasta, .fa, .fna, .faa | FASTA (format_1929) |
| `bam` | binary SAM alignment file | .bam | BAM (format_2572) |
| `vcf` | variant call format text | .vcf | VCF (format_3016) |
| `bed` | BED interval text | .bed | BED (format_3003) |
| `gff3` | GFF version 3 feature text; a bare .gff is not inferred because it may be GFF2 | .gff3 | GFF3 (format_1975) |
| `genbank` | GenBank flat file | .gb, .gbk | GenBank format (format_1936) |
| `tsv` | tab-separated values | .tsv | TSV (format_3475) |
| `csv` | comma-separated values | .csv | CSV (format_3752) |
| `json` | JSON text | .json | JSON (format_3464) |
| `markdown` | Markdown text | .md | - |
| `pdf` | PDF document | .pdf | PDF (format_3508) |
| `png` | PNG image | .png | PNG (format_3603) |
| `h5ad` | AnnData HDF5 file | .h5ad | - |
| `html` | HTML document or interactive report | .html, .htm | HTML (format_2331) |
| `cram` | reference-compressed alignment file | .cram | CRAM (format_3462) |
| `bigwig` | indexed binary genome track for dense continuous values | .bigwig, .bigWig, .bw | bigWig (format_3006) |
| `bedgraph` | bedGraph text for continuous values over genomic intervals | .bedgraph, .bedGraph | bedgraph (format_3583) |
| `gtf` | Gene Transfer Format feature annotation text | .gtf | GTF (format_2306) |
| `gff` | version-unspecified General Feature Format text | .gff | GFF (format_2305) |
| `hdf5` | generic HDF5 container that is not the more specific h5ad format | .h5, .hdf5 | HDF5 (format_3590) |
| `mtx` | Matrix Market coordinate or array text | .mtx | - |
| `newick` | Newick text representation of a tree | .newick, .nwk, .tree | newick (format_1910) |
| `rds` | serialized single R object | .rds | R file format (format_3554) |
| `encode_peak` | ENCODE narrow, broad or gapped peak interval format | .narrowPeak, .broadPeak, .gappedPeak | - |
| `qiime2_artifact` | QIIME 2 artifact or visualization archive | .qza, .qzv | - |
| `parquet` | Apache Parquet columnar data file | .parquet | - |
| `tiff` | TIFF image, including OME-TIFF microscopy images | .tif, .tiff, .ome.tif, .ome.tiff | - |

## 작업(operation)

| key | 정의 | EDAM |
|---|---|---|
| `statistical_analysis` | a statistical test or model fit on data | Statistical calculation (operation_2238) |
| `de_analysis` | estimating and statistically testing expression differences for genes or transcripts across specified conditions or contrasts | Differential gene expression profiling (operation_3223) |
| `enrichment_analysis` | testing gene lists or ranked genes for enrichment of biological functions, pathways or other gene sets | Enrichment analysis (operation_3501) |
| `read_mapping` | aligning reads to a reference | Read mapping (operation_3198) |
| `variant_calling` | calling variants from alignments | Variant calling (operation_3227) |
| `data_retrieval` | fetching data from a public or local source | Data retrieval (operation_2422) |
| `quality_control` | assessing data or result quality with explicit metrics, checks or thresholds | Sequencing quality control (operation_3218) |
| `read_preprocessing` | preparing sequence reads by demultiplexing, adapter trimming, quality filtering or contaminant removal | - |
| `quantification` | estimating counts, abundances or signal levels for biological features | Quantification (operation_3799) |
| `normalization` | adjusting measurements to make samples or observations quantitatively comparable | Standardisation and normalisation (operation_3435) |
| `genome_assembly` | constructing genome or metagenome sequences from reads | Genome assembly (operation_0525) |
| `transcriptome_assembly` | reconstructing transcript models from RNA sequencing reads or alignments | Transcriptome assembly (operation_3258) |
| `sequence_annotation` | assigning biological features or functions to assembled or reference sequences | Sequence annotation (operation_0361) |
| `peak_calling` | detecting genomic intervals with enriched chromatin accessibility or binding signal | Peak calling (operation_3222) |
| `differential_abundance_analysis` | testing non-gene features for abundance differences across specified conditions or contrasts | - |
| `methylation_calling` | estimating methylation state or level at cytosines or genomic intervals | Methylation calling (operation_3919) |
| `taxonomic_profiling` | assigning taxa to reads or features and optionally estimating their abundances | Taxonomic classification (operation_3460) |
| `splicing_analysis` | measuring or testing alternative transcript splicing or isoform usage | Splicing analysis (operation_2499) |
| `variant_annotation` | adding predicted consequences, genes, frequencies or clinical knowledge to called variants | Variant effect prediction (operation_0331) |
| `phasing` | assigning alleles or variants to haplotypes | Phasing (operation_3454) |
| `consensus_generation` | deriving a representative consensus sequence from aligned or clustered reads | - |
| `cell_segmentation` | identifying cell or nucleus boundaries from images or spatial molecular coordinates | - |

## 공개 자원

| 결과 | 자원 | 쓰임 |
|---|---|---|
| variants | Ensembl VEP | consequence and transcript effect (labhq_annot vep tool) |
| variants | gnomAD | population allele frequency (labhq_annot gnomad tool) |
| variants | ClinVar | clinical significance (labhq_annot clinvar tool) |
| variants | dbSNP | rsID and known variant identity |
| variants | AlphaGenome | predicted regulatory effect of non-coding variants (labhq_annot alphagenome tool when its key is set) |
| variants | COSMIC | somatic mutation recurrence in cancer |
| genes | Open Targets | target-disease association evidence |
| genes | DisGeNET | gene-disease associations |
| genes | GTEx | tissue expression and eQTL (labhq_annot gtex tool) |
| genes | Human Protein Atlas | tissue and cell-type protein expression |
| genes | MSigDB | gene sets for enrichment |
| genes | Reactome | pathway membership |
| genes | STRING | protein interaction partners |
| regions | ChIP-Atlas | public ChIP/ATAC peaks and enrichment at regions or genes (labhq_annot chipatlas and chipatlas_targets tools) |
| regions | ENCODE | candidate cis-regulatory elements (labhq_annot encode tool) |
| regions | JASPAR | transcription factor motifs |
| proteins | UniProt | protein function and features |
| proteins | AlphaFold DB | predicted structure |
| compounds | ChEMBL | bioactivity and mechanism |
| literature | PubMed | published evidence |
| literature | bioRxiv | recent preprints |

EDAM 이름과 id는 CC BY-SA 4.0입니다([NOTICE](../labhq/vocab/NOTICE.md)).
