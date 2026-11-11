# Dexamethasone response in airway smooth muscle (synthetic evaluation bundle)

## Question
Does dexamethasone change gene expression in four airway smooth muscle lines (8 libraries, paired by line)?

## Methods
Salmon quantification of 8 libraries; DESeq2 with design `~ cell + dex`, contrast dexamethasone (trt) versus untreated
(untrt), so a positive log2FoldChange means higher after dexamethasone. Significance is pvalue < 0.05.
A sensitivity model without the cell term was also fitted (`sensitivity.csv`).

## Results
- Mapping rate was 93.91 to 95.68 percent across all 8 libraries; no library was excluded.
- All six known responders increased after dexamethasone: DUSP1 (log2FC 2.97), KLF15 (5.41), PER1 (3.12),
  TSC22D3 (3.20), ZBTB16 (6.26) and FKBP5 (4.02), each padj < 1e-18.
- GENE_A decreased (log2FC -1.85, padj 4.3e-07). GENE_B and GENE_D also changed (pvalue < 0.05); two of the four other genes changed (50%).
- Without the cell term, 2408 genes had padj < 0.05 compared with 3657 with it; all six responders stayed up.

## Agreement with the literature
DUSP1 induction matches the original study (log2FC 2.97 there as well) [himes]. An independent dataset in bronchial
cells (GSE-SYNTH-2) reports the same responders [replication].

## Conclusion
Dexamethasone induces the canonical glucocorticoid response genes in these cells; the result holds with and without
the cell-line covariate.
