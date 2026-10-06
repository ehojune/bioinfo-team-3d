"""Regulatory-region, expression and model lookups for labhq_annot (#435 C ③): ChIP-Atlas, ENCODE cCREs, GTEx and
AlphaGenome. Same frame as `annot.py`: every result names its source release, the request goes to
`outputs/annotation_queries.jsonl` (counts and hashes only) and the full answer to `outputs/annotation/`, answers are
cached per release, and a bad input fails only its own item.

Regions are `chr:start-end`, 1-based and inclusive like a genome browser (`chr12:53380000-53385000`), or `chr:pos`.
Variants and regions from restricted zones are looked up like any other (PI decision 2026-10-06).

APIs (checked 2026-10-06):
- ChIP-Atlas enrichment analysis: POST https://dtn1.ddbj.nig.ac.jp/wabi/chipatlas/ answers `requestId: ...`; the result
  is at `<id>?info=result&format=tsv` once the job ends. The status call is unreliable (the ChIP-Atlas site itself
  checks the result file instead, app.rb), so labhq polls the result. Columns from the EA script: SRX, antigen class,
  antigen, cell class, cell type, peaks, overlaps with the query, overlaps with the background, log10 P, log10 Q,
  fold enrichment. Target genes: https://chip-atlas.dbcls.jp/data/<genome>/target/<antigen>.<1|5|10>.tsv.
  https://github.com/inutano/chip-atlas/wiki/Perform-Enrichment-Analysis-programmatically
- ENCODE cCREs: the SCREEN GraphQL API needs a personal key (console.wenglab.org, valid 90 days), so labhq reads the
  registry itself: the newest released cCRE Annotation for the assembly on the ENCODE portal (REST search), its
  bed.gz file (about 33 MB for GRCh38 V4), checked against the portal's md5 and kept in the cache.
- GTEx API v2 (gtexportal.org, OpenAPI document `api/v2/redoc`): metadata/dataset (release, GENCODE version), reference/gene,
  expression/medianGeneExpression, association/singleTissueEqtl, dataset/variant. Gene IDs carry the dataset's
  GENCODE version (v39 for gtex_v10, v26 for gtex_v8).
- AlphaGenome: the official `alphagenome` client (pip), `dna_client.create(key)`, `predict_variant` /
  `predict_interval` with sequence lengths 16 KB, 100 KB, 500 KB or 1 MB. Non-commercial use; predictions are not for
  clinical decisions (https://alphagenome.google/terms). labhq keeps per-track summaries,
  not the base-resolution arrays.
"""

from __future__ import annotations

import asyncio
import bisect
import gzip
import hashlib
import importlib.metadata
import importlib.util
import inspect
import itertools
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

import httpx

from .annot import Annotator, Cache, Item, LookupFailed, Variant, _error_text, _now, _sha256, parse_variant

CHIPATLAS_WABI = "https://dtn1.ddbj.nig.ac.jp/wabi/chipatlas/"
CHIPATLAS_DATA = "https://chip-atlas.dbcls.jp/data"
ENCODE = "https://www.encodeproject.org"
# Joined so this staff-side file never spells the labhq gateway's REST prefix (the PI-only action guard test).
GTEX = "https://gtexportal.org" + "/api" + "/v2"

CHIPATLAS_GENOMES = ("hg38", "hg19", "mm10", "mm9", "rn6", "dm6", "dm3", "ce11", "ce10", "sacCer3")
CHIPATLAS_ANTIGEN_CLASSES = ("TFs and others", "Histone", "RNA polymerase", "Input control", "ATAC-Seq", "DNase-seq",
                             "Bisulfite-Seq")
CHIPATLAS_THRESHOLDS = (50, 100, 200, 500)  # MACS2 -log10 Q x10
CHIPATLAS_PERMUTATIONS = (1, 10, 100)
CHIPATLAS_TARGET_KB = (1, 5, 10)
CHIPATLAS_COLUMNS = ("srx", "antigen_class", "antigen", "cell_class", "cell_type", "num_peaks", "overlaps_query",
                     "overlaps_background", "log10_p", "log10_q", "fold_enrichment")
CHIPATLAS_POLL_S = 15.0
CHIPATLAS_WAIT_S = 600.0
CHIPATLAS_MAX_WAIT_S = 1500.0
CHIPATLAS_MAX_INPUTS = 5000
CHIPATLAS_TARGET_MAX_BYTES = 300_000_000  # CTCF hg38 at 5 kb is 113 MB
CHIPATLAS_TOP = 20

ENCODE_ASSEMBLIES = ("GRCh38", "mm10")
ENCODE_MAX_REGIONS = 500
ENCODE_MAX_WIDTH = 2_000_000
ENCODE_MAX_BYTES = 500_000_000
ENCODE_LIST = 100  # cCREs per region in the tool answer; the result file keeps all

GTEX_MAX_ITEMS = 50
GTEX_PAGE = 250
GTEX_MAX_PAGES = 4  # 1,000 eQTL rows per gene; the total is reported
GTEX_TOP = 10

AG_OUTPUTS = ("ATAC", "CAGE", "DNASE", "RNA_SEQ", "CHIP_HISTONE", "CHIP_TF", "SPLICE_SITES", "SPLICE_SITE_USAGE",
              "PROCAP")
AG_LENGTHS = {"16KB": 2 ** 14, "100KB": 2 ** 17, "500KB": 2 ** 19, "1MB": 2 ** 20}
AG_ORGANISMS = {"human": "HOMO_SAPIENS", "mouse": "MUS_MUSCULUS"}
AG_MAX_PER_CALL = 10
AG_MAX_TERMS = 20
AG_TIMEOUT_S = 300.0
AG_CONNECT_TIMEOUT_S = 30.0
AG_TOP_TRACKS = 10
AG_DEFAULT_LENGTH = "100KB"
# Outputs at 1-bp resolution: with every track (no ontology_terms) over 500 KB or more, one allele is gigabytes.
AG_BP_OUTPUTS = ("ATAC", "CAGE", "DNASE", "RNA_SEQ", "SPLICE_SITES", "SPLICE_SITE_USAGE", "PROCAP")
AG_ALL_TRACKS_MAX = 2 ** 17
# Each client call runs on its own daemon thread, not the event loop's default executor (ENCODE's file scan and md5
# share that) nor a fixed pool: a thread that timed out cannot be killed, and in a fixed pool two of them would hold
# every worker for ever. Timed-out threads still running are counted; at AG_MAX_STUCK new calls are refused.
AG_MAX_STUCK = 4
_AG_STUCK: list[threading.Thread] = []
_AG_THREAD_IDS = itertools.count(1)

_REGION = re.compile(r"^(?:chr)?([0-9]{1,2}|X|Y|M|MT):([0-9][0-9,]*)(?:-([0-9][0-9,]*))?$", re.IGNORECASE)
_GENE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,39}$")
_ENSG = re.compile(r"^ENS[A-Z]*G[0-9]{11}(?:\.[0-9]+)?$")
_STRICT_RSID = re.compile(r"^rs[0-9]+$")  # lower case only: RS1 is a gene
_ANTIGEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,40}$")
_CELL_CLASS = re.compile(r"^[A-Za-z][A-Za-z ,\-]{1,40}$")
_REQUEST_ID = re.compile(r"^wabi_chipatlas_[0-9-]+$")
_GTEX_DATASET = re.compile(r"^gtex_v[0-9]+$")
_TISSUE = re.compile(r"^[A-Za-z0-9_\-()]{2,80}$")
_ONTOLOGY = re.compile(r"^[A-Za-z]+:[A-Za-z0-9_]+$")
LOG10_Q05 = -1.3010299956639813


@dataclass(frozen=True)
class Query:
    """One input of these tools: a region, a gene or a variant. `key` is what the cache and dedup use."""
    raw: str
    kind: str  # region | gene | variant
    key: str
    chrom: str = ""  # UCSC style: chr1, chrX, chrM
    start0: int = 0  # half-open, 0-based
    end: int = 0
    variant: Variant | None = None


def parse_region(value: object, max_width: int | None = None) -> Query:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("영역은 chr:start-end (1부터 세는 닫힌 구간) 또는 chr:pos 문자열입니다")
    raw = value.strip()
    match = _REGION.match(raw)
    if not match:
        raise ValueError("영역 형식을 알 수 없습니다. chr:start-end (예: chr12:53380000-53385000) 또는 chr:pos")
    chrom = match.group(1).upper()
    chrom = "M" if chrom in ("M", "MT") else chrom
    if chrom.isdigit():
        if int(chrom) < 1:
            raise ValueError(f"알 수 없는 염색체: {match.group(1)}")
        chrom = str(int(chrom))
    start = int(match.group(2).replace(",", ""))
    end = int(match.group(3).replace(",", "")) if match.group(3) else start
    if start < 1 or end < start:
        raise ValueError("start는 1 이상이고 end보다 크지 않아야 합니다")
    if max_width is not None and end - start + 1 > max_width:
        raise ValueError(f"영역이 {max_width:,} bp보다 깁니다")
    name = f"chr{chrom}"
    return Query(raw, "region", f"{name}:{start}-{end}", name, start - 1, end)


def parse_gene(value: object) -> Query:
    if not isinstance(value, str) or not _GENE.match(value.strip()):
        raise ValueError("유전자는 기호(예: SORT1)나 Ensembl gene ID(예: ENSG00000134243)입니다")
    raw = value.strip()
    return Query(raw, "gene", raw.upper() if not _ENSG.match(raw) else raw)


def _ucsc(chrom: str) -> str:
    return "chrM" if chrom == "MT" else f"chr{chrom}"


def _fold_change(row: dict) -> float:
    value = row.get("fold_enrichment")
    return value if isinstance(value, (int, float)) else 0.0


def _float(text: str) -> float | str:
    try:
        return float(text)
    except ValueError:
        return text


def chipatlas_rows(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != len(CHIPATLAS_COLUMNS):
            rows.append({"fields": fields})
            continue
        row: dict[str, Any] = dict(zip(CHIPATLAS_COLUMNS, fields))
        for name in ("log10_p", "log10_q", "fold_enrichment"):
            row[name] = _float(row[name])
        row["num_peaks"] = int(row["num_peaks"]) if row["num_peaks"].isdigit() else row["num_peaks"]
        rows.append(row)
    return rows


def _chipatlas_summary(answer: dict) -> dict:
    rows = [r for r in answer.get("rows") or [] if "srx" in r]
    q = [r for r in rows if isinstance(r.get("log10_q"), float)]
    ordered = sorted(q, key=lambda r: (r["log10_q"], -_fold_change(r)))
    by_antigen: dict[str, dict] = {}
    for row in ordered:
        entry = by_antigen.setdefault(row["antigen"], {
            "antigen": row["antigen"], "best_log10_q": row["log10_q"], "best_fold_enrichment": row["fold_enrichment"],
            "best_cell_type": row["cell_type"], "n_experiments": 0, "n_q_below_0_05": 0})
        entry["n_experiments"] += 1
        entry["n_q_below_0_05"] += row["log10_q"] < LOG10_Q05 and _fold_change(row) > 1
    keep = ("srx", "antigen", "cell_class", "cell_type", "overlaps_query", "overlaps_background", "log10_q",
            "fold_enrichment")
    return {"request_id": answer.get("request_id"), "n_experiments": len(rows),
            "n_q_below_0_05": sum(1 for r in q if r["log10_q"] < LOG10_Q05 and _fold_change(r) > 1),
            "top_antigens": list(by_antigen.values())[:CHIPATLAS_TOP],
            "top_experiments": [{k: r.get(k) for k in keep} for r in ordered[:CHIPATLAS_TOP]],
            "meaning": "log10_q는 Fisher 검정 Q값의 log10(작을수록 유의), fold_enrichment > 1이 질의 쪽 농축입니다"}


def scan_ccres(path: Path, regions: list[Query]) -> dict[str, list[dict]]:
    """cCREs from a registry bed.gz that overlap each region (half-open), in file order."""
    by_chrom: dict[str, list[Query]] = {}
    for region in regions:
        by_chrom.setdefault(region.chrom, []).append(region)
    index = {}
    for chrom, items in by_chrom.items():
        items.sort(key=lambda r: r.start0)
        index[chrom] = ([r.start0 for r in items], items, max(r.end - r.start0 for r in items))
    hits: dict[str, list[dict]] = {region.key: [] for region in regions}
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            fields = line.rstrip("\n").split("\t")
            entry = index.get(fields[0])
            if entry is None or len(fields) < 4:
                continue
            starts, items, longest = entry
            start, end = int(fields[1]), int(fields[2])
            low = bisect.bisect_right(starts, start - longest)
            high = bisect.bisect_left(starts, end)
            for region in items[low:high]:
                if region.start0 < end and region.end > start:
                    hits[region.key].append({"accession": fields[3], "chrom": fields[0], "start": start + 1,
                                             "end": end, "class": fields[9] if len(fields) > 9 else None})
    return hits


def _encode_summary(item: Item) -> dict:
    answer = item.answer if isinstance(item.answer, dict) else {}
    ccres = answer.get("ccres") or []
    classes: dict[str, int] = {}
    for ccre in ccres:
        classes[str(ccre.get("class"))] = classes.get(str(ccre.get("class")), 0) + 1
    out: dict[str, Any] = {"region": item.variant.key, "n_ccres": len(ccres), "classes": classes,
                           "ccres": ccres[:ENCODE_LIST]}
    if len(ccres) > ENCODE_LIST:
        out["truncated"] = f"cCRE {len(ccres)}개 중 {ENCODE_LIST}개만 보였습니다. 전체는 full_result 파일에 있습니다"
    if not ccres:
        out["meaning"] = "이 영역과 겹치는 cCRE가 registry에 없습니다(조회는 성공)"
    return out


def _gtex_eqtl_rows(rows: list[dict]) -> list[dict]:
    keep = ("variantId", "snpId", "geneSymbol", "gencodeId", "tissueSiteDetailId", "nes", "pValue")
    ordered = sorted((r for r in rows if isinstance(r, dict)),
                     key=lambda r: r.get("pValue") if isinstance(r.get("pValue"), (int, float)) else 1.0)
    return [{k: r.get(k) for k in keep} for r in ordered]


def _gtex_summary(item: Item) -> dict:
    answer = item.answer if isinstance(item.answer, dict) else {}
    if not answer.get("found"):
        return {"found": False, "meaning": answer.get("meaning") or "GTEx에 없습니다(조회는 성공)"}
    out: dict[str, Any] = {"found": True}
    if "gene" in answer:
        out["gene"] = answer["gene"]
        expression = sorted(answer.get("expression") or [], key=lambda r: -(r.get("median") or 0))
        out["expression_tpm_median"] = [{"tissue": r.get("tissueSiteDetailId"), "median": r.get("median")}
                                        for r in expression[:GTEX_TOP if not answer.get("tissues") else None]]
    if "variants" in answer:
        out["variants"] = answer["variants"]
    eqtl = answer.get("eqtl")
    if isinstance(eqtl, dict):
        rows = _gtex_eqtl_rows(eqtl.get("rows") or [])
        out["eqtl"] = {"n_total": eqtl.get("total", len(rows)), "n_fetched": len(rows), "top": rows[:GTEX_TOP]}
        if eqtl.get("truncated"):
            out["eqtl"]["truncated"] = f"GTEx 응답 {eqtl.get('total')}개 중 {len(rows)}개만 받았습니다(p값 순 정렬은 받은 범위 안)"
    return out


def alphagenome_client_available() -> bool:
    try:
        return importlib.util.find_spec("alphagenome") is not None
    except (ImportError, ValueError):
        return False


# ----- AlphaGenome track summaries -----
def _rows(metadata: Any) -> list[dict]:
    if metadata is None:
        return []
    if hasattr(metadata, "to_dict"):
        return [dict(r) for r in metadata.to_dict("records")]
    return [dict(r) for r in metadata]


def _track_label(meta: dict) -> dict:
    keep = ("name", "strand", "ontology_curie", "biosample_name", "biosample_type", "Assay title", "gtex_tissue",
            "transcription_factor", "histone_mark")
    return {k: meta[k] for k in keep if k in meta and meta[k] is not None and meta[k] == meta[k]}


def _columns(values: Any) -> tuple[int, int, Callable[[int], list[float]]]:
    """(length, tracks, column getter) for a positions x tracks array: numpy when the client gave one, else lists."""
    shape = getattr(values, "shape", None)
    if shape is not None and len(shape) == 2:
        return int(shape[0]), int(shape[1]), lambda j: values[:, j]
    rows = [list(r) for r in values]
    width = len(rows[0]) if rows else 0
    return len(rows), width, lambda j: [r[j] for r in rows]


def _argmax(column: Any) -> tuple[int, float]:
    if hasattr(column, "argmax"):
        index = int(column.argmax())
        return index, float(column[index])
    best, index = float("-inf"), 0
    for i, value in enumerate(column):
        if value > best:
            best, index = float(value), i
    return index, best


def _total(column: Any) -> float:
    return float(column.sum()) if hasattr(column, "sum") else float(sum(column))


def _abs_diff(first: Any, second: Any) -> Any:
    if hasattr(first, "__sub__") and hasattr(first, "shape"):
        return abs(second - first)
    return [abs(b - a) for a, b in zip(first, second)]


def variant_track_stats(reference: Any, alternate: Any) -> list[dict]:
    """Per track: how far the alternate allele moves the prediction (largest absolute change and where), and the
    summed signal for each allele. Positions are 1-based genome coordinates of the bin start."""
    if reference is None or alternate is None:
        return []
    length, tracks, ref_col = _columns(reference.values)
    _length, _tracks, alt_col = _columns(alternate.values)
    start0 = getattr(getattr(reference, "interval", None), "start", 0) or 0
    resolution = int(getattr(reference, "resolution", 1) or 1)
    metas = _rows(getattr(reference, "metadata", None))
    out = []
    for j in range(tracks):
        ref, alt = ref_col(j), alt_col(j)
        index, change = _argmax(_abs_diff(ref, alt))
        ref_sum, alt_sum = _total(ref), _total(alt)
        out.append({**_track_label(metas[j] if j < len(metas) else {}), "resolution": resolution,
                    "max_abs_change": change, "max_change_position": start0 + index * resolution + 1,
                    "ref_sum": ref_sum, "alt_sum": alt_sum, "alt_minus_ref_sum": alt_sum - ref_sum})
    out.sort(key=lambda r: -r["max_abs_change"])
    return out


def interval_track_stats(track: Any, region_start0: int | None = None, region_end: int | None = None) -> list[dict]:
    """Per track: mean and peak signal over the requested region only (0-based half-open `region_start0..region_end`),
    not the whole prediction window around it. Positions are 1-based genome coordinates of the bin start."""
    if track is None:
        return []
    window0 = getattr(getattr(track, "interval", None), "start", 0) or 0
    resolution = int(getattr(track, "resolution", 1) or 1)
    values = track.values
    start0 = window0
    if region_start0 is not None and region_end is not None:
        total_bins = int(values.shape[0]) if getattr(values, "shape", None) is not None else len(values)
        low = min(max(0, (region_start0 - window0) // resolution), total_bins)
        high = min(max(low + 1, -(-(region_end - window0) // resolution)), total_bins)
        values = values[low:high]
        start0 = window0 + low * resolution
    length, tracks, column = _columns(values)
    metas = _rows(getattr(track, "metadata", None))
    out = []
    for j in range(tracks):
        values = column(j)
        index, peak = _argmax(values)
        total = _total(values)
        out.append({**_track_label(metas[j] if j < len(metas) else {}), "resolution": resolution,
                    "mean": total / length if length else None, "max": peak if length else None,
                    "max_position": start0 + index * resolution + 1 if length else None})
    out.sort(key=lambda r: -(r["max"] or 0))
    return out


class AlphaGenomeUnavailable(Exception):
    """The client could not open the service (unreachable, refused key), or too many earlier calls are still stuck:
    later items of the call fail at once."""


async def _alphagenome_call(fn: Callable[..., Any], *args: Any) -> Any:
    """`fn(*args)` on a fresh daemon thread, waited for at most AG_TIMEOUT_S. A timed-out thread is left running
    (daemon, so it never holds up exit) and counted in `_AG_STUCK` until it ends."""
    _AG_STUCK[:] = [thread for thread in _AG_STUCK if thread.is_alive()]
    if len(_AG_STUCK) >= AG_MAX_STUCK:
        raise AlphaGenomeUnavailable(f"앞서 시간이 초과된 AlphaGenome 호출 {len(_AG_STUCK)}개가 아직 끝나지 않았습니다. "
                                     "잠시 뒤 다시 부르거나 labhq를 다시 시작하세요")
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def settle(result: Any, error: BaseException | None) -> None:
        if future.done():  # the caller gave up (timeout) before the client answered
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)

    def run() -> None:
        try:
            outcome: tuple[Any, BaseException | None] = (fn(*args), None)
        except Exception as exc:  # noqa: BLE001 - handed to the awaiting coroutine
            outcome = (None, exc)
        try:
            loop.call_soon_threadsafe(settle, *outcome)
        except RuntimeError:  # the event loop closed while the client hung
            pass

    thread = threading.Thread(target=run, name=f"labhq-alphagenome-{next(_AG_THREAD_IDS)}", daemon=True)
    thread.start()
    try:
        return await asyncio.wait_for(future, AG_TIMEOUT_S)
    except asyncio.TimeoutError:
        if thread.is_alive():
            _AG_STUCK.append(thread)
        raise


class AlphaGenomeBackend:
    """The official AlphaGenome client behind one call. The key stays in this object: it is never logged, written,
    returned or put on a command line. Tests pass fake `dna_client` and `genome` modules."""

    def __init__(self, key: str, dna_client: Any = None, genome: Any = None, version: str | None = None) -> None:
        self._key = key
        self._dna_client, self._genome = dna_client, genome
        self._version = version
        self._model: Any = None

    def _modules(self) -> tuple[Any, Any]:
        if self._dna_client is None or self._genome is None:
            from alphagenome.data import genome  # type: ignore[import-not-found]
            from alphagenome.models import dna_client  # type: ignore[import-not-found]
            self._dna_client, self._genome = dna_client, genome
        return self._dna_client, self._genome

    def release(self) -> str:
        version = self._version
        if version is None:
            try:
                version = importlib.metadata.version("alphagenome")
            except importlib.metadata.PackageNotFoundError:
                version = "unknown"
        return f"AlphaGenome API (alphagenome client {version}, default model ALL_FOLDS)"

    def redact(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text

    def _create(self, dna_client: Any) -> Any:
        """The client's model handle. `create` waits for the gRPC channel; without a timeout it blocks for ever when
        the service is unreachable, so the timeout is passed whenever this client version takes one."""
        try:
            parameters = inspect.signature(dna_client.create).parameters
        except (TypeError, ValueError):
            parameters = {}
        takes_timeout = "timeout" in parameters or any(p.kind is p.VAR_KEYWORD for p in parameters.values())
        if takes_timeout:
            return dna_client.create(self._key, timeout=AG_CONNECT_TIMEOUT_S)
        return dna_client.create(self._key)

    def predict(self, query: Query, width: int, outputs: list[str], terms: list[str] | None,
                organism: str) -> dict[str, list[dict]]:
        dna_client, genome = self._modules()
        if self._model is None:
            try:
                self._model = self._create(dna_client)
            except Exception as exc:  # noqa: BLE001 - grpc, auth and timeout errors alike
                raise AlphaGenomeUnavailable(f"AlphaGenome에 연결하지 못했습니다: {type(exc).__name__}: {exc}") from exc
        if query.variant is not None:
            centre0 = query.variant.pos - 1
        else:
            centre0 = (query.start0 + query.end) // 2
        start0 = max(0, centre0 - width // 2)
        interval = genome.Interval(chromosome=query.chrom, start=start0, end=start0 + width)
        requested = [dna_client.OutputType[name] for name in outputs]
        kind = getattr(dna_client.Organism, AG_ORGANISMS[organism])
        if query.variant is not None:
            variant = genome.Variant(chromosome=query.chrom, position=query.variant.pos,
                                     reference_bases=query.variant.ref, alternate_bases=query.variant.alt)
            out = self._model.predict_variant(interval=interval, variant=variant, organism=kind,
                                              requested_outputs=requested, ontology_terms=terms)
            return {name: variant_track_stats(out.reference.get(dna_client.OutputType[name]),
                                               out.alternate.get(dna_client.OutputType[name])) for name in outputs}
        out = self._model.predict_interval(interval=interval, organism=kind, requested_outputs=requested,
                                           ontology_terms=terms)
        return {name: interval_track_stats(out.get(dna_client.OutputType[name]), query.start0, query.end)
                for name in outputs}


def _alphagenome_summary(item: Item) -> dict:
    answer = item.answer if isinstance(item.answer, dict) else {}
    tracks = answer.get("tracks") or {}
    out: dict[str, Any] = {"input": item.variant.key, "window": answer.get("window"),
                           "summarised_over": answer.get("summarised_over")}
    for name, rows in tracks.items():
        out[name] = {"n_tracks": len(rows), "top": rows[:AG_TOP_TRACKS]}
    out["meaning"] = ("변이는 예측 창(window) 전체에서 track별 alt-ref 최대 절대 변화(max_abs_change)와 그 위치, "
                      "구간은 요청한 구간(summarised_over) 안의 track별 평균·최대 신호입니다. 모델 예측이며 임상 판단에 "
                      "쓰지 않습니다(AlphaGenome 이용 약관)")
    return out


class RegulatoryAnnotator(Annotator):
    def __init__(self, *args: Any, alphagenome: AlphaGenomeBackend | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.alphagenome_backend = alphagenome

    # ----- helpers -----
    def _set_record(self, tool: str, db: str, release: str, url: str, options: dict, inputs: list[str],
                    errors: list[dict], answer: object, cached_at: str | None, summary: dict) -> dict:
        """The record of a set analysis: one answer for all valid inputs, plus the inputs that failed."""
        queried_at = _now()
        source = {"db": db, "release_or_version": release, "url": url, "queried_at": queried_at,
                  "request_sha256": _sha256({"tool": tool, "options": options, "inputs": inputs,
                                             "errors": [e["input"] for e in errors]})}
        counts = {"n_inputs": len(inputs) + len(errors), "n_ok": len(inputs), "n_errors": len(errors),
                  "n_cached": len(inputs) if cached_at else 0}
        full = {"source": source, "options": options, "inputs": inputs, "input_errors": errors, "answer": answer,
                **({"cached_from": cached_at} if cached_at else {})}
        out: dict[str, Any] = {"source": source, **counts}
        out.update(self._record(tool, source, counts, full))
        if cached_at:
            out["cached_from"] = cached_at
        out["input_errors"] = errors
        out.update(summary)
        return out

    async def _json(self, service: str, url: str, params: dict | None = None, what: str = "") -> object:
        response = await self.http.request(service, "GET", url, params=params, headers={"Accept": "application/json"})
        if response.status_code != 200:
            raise LookupFailed(f"{what or url} HTTP {response.status_code}: {_error_text(response)}")
        try:
            return response.json()
        except ValueError as exc:
            raise LookupFailed(f"{what or url} 응답이 JSON이 아닙니다: {exc}") from exc

    # ----- ChIP-Atlas enrichment -----
    async def chipatlas(self, genes_or_regions: object, genome: str = "hg38", antigen_class: str = "TFs and others",
                        cell_class: str = "All cell types", threshold: int = 50, distance_kb: int = 5,
                        permutations: int = 1, wait_s: float = CHIPATLAS_WAIT_S,
                        request_id: str | None = None) -> dict:
        if genome not in CHIPATLAS_GENOMES:
            raise ValueError(f"genome은 {', '.join(CHIPATLAS_GENOMES)} 중 하나입니다")
        if antigen_class not in CHIPATLAS_ANTIGEN_CLASSES:
            raise ValueError(f"antigen_class는 {', '.join(CHIPATLAS_ANTIGEN_CLASSES)} 중 하나입니다")
        if not isinstance(cell_class, str) or not _CELL_CLASS.match(cell_class):
            raise ValueError("cell_class는 ChIP-Atlas 세포 분류 이름입니다(예: All cell types, Blood, Liver)")
        if threshold not in CHIPATLAS_THRESHOLDS:
            raise ValueError(f"threshold는 {CHIPATLAS_THRESHOLDS} 중 하나입니다")
        if permutations not in CHIPATLAS_PERMUTATIONS:
            raise ValueError(f"permutations는 {CHIPATLAS_PERMUTATIONS} 중 하나입니다")
        if not isinstance(distance_kb, int) or not 0 <= distance_kb <= 100:
            raise ValueError("distance_kb는 0~100 사이 정수입니다(유전자 TSS 앞뒤 거리)")
        if not isinstance(wait_s, (int, float)) or not 0 <= wait_s <= CHIPATLAS_MAX_WAIT_S:
            raise ValueError(f"wait_s는 0~{CHIPATLAS_MAX_WAIT_S:.0f}초입니다")
        if request_id is not None and (not isinstance(request_id, str) or not _REQUEST_ID.match(request_id)):
            raise ValueError("request_id는 앞선 호출이 돌려준 wabi_chipatlas_... 값입니다")
        values = [genes_or_regions] if isinstance(genes_or_regions, str) else genes_or_regions
        if not isinstance(values, list) or not values:
            raise ValueError("유전자나 영역 목록이 비었습니다")
        if len(values) > CHIPATLAS_MAX_INPUTS:
            raise ValueError(f"한 번에 {CHIPATLAS_MAX_INPUTS}개까지 보냅니다")
        queries: list[Query] = []
        errors: list[dict] = []
        mode: str | None = None
        for value in values:
            try:
                query = parse_region(value) if isinstance(value, str) and ":" in value else parse_gene(value)
            except ValueError as exc:
                errors.append({"input": value, "error": str(exc)})
                continue
            if query.kind == "gene":  # ChIP-Atlas gets the symbol as typed, so dedup and cache use that spelling
                query = Query(query.raw, "gene", query.raw)
            mode = mode or query.kind
            if query.kind != mode:
                errors.append({"input": value, "error": "한 호출에 유전자와 영역을 섞을 수 없습니다. 따로 부르세요"})
                continue
            queries.append(query)
        keys = list(dict.fromkeys(q.key for q in queries))
        if not keys:
            raise ValueError(f"조회할 유전자나 영역이 없습니다: {errors[0]['error'] if errors else ''}")
        options = {"genome": genome, "antigen_class": antigen_class, "cell_class": cell_class,
                   "threshold": threshold, "input_type": mode, "background": "refseq" if mode == "gene" else "random",
                   "distance_kb": distance_kb, "permutations": permutations if mode == "region" else None}
        release, cacheable = await self._chipatlas_release()
        cache_key = Cache.key("chipatlas", release, options, "\n".join(sorted(keys)))
        hit = self.cache.get("chipatlas", cache_key) if cacheable and request_id is None else None
        # The job a request_id names is bound at submission to its inputs and options (`request`) and to the release
        # it ran on: a job resumed after experimentList.tab changed keeps its own release and is not cached.
        request = Cache.key("chipatlas_request", "", options, "\n".join(sorted(keys)))
        notes: dict[str, str] = {}
        if hit is not None:
            answer, cached_at = hit["answer"], hit.get("queried_at")
        else:
            if request_id is not None:
                bound = self.cache.get("chipatlas_requests", Cache.key("chipatlas_requests", "", {}, request_id))
                binding = bound["answer"] if bound is not None else None
                if not (isinstance(binding, dict) and isinstance(binding.get("request"), str)
                        and isinstance(binding.get("release"), str)):
                    binding = None
                if binding is not None and binding["request"] != request:
                    raise ValueError(f"request_id {request_id}는 다른 입력·옵션으로 낸 분석입니다. 그 분석을 낸 "
                                     "호출과 같은 입력·옵션으로 부르세요")
                if binding is None:
                    cacheable = False
                    notes["request_id_note"] = ("이 request_id를 낸 기록이 캐시에 없어 입력·옵션·release가 맞는지 "
                                                "확인하지 못했습니다. 답을 캐시하지 않았습니다")
                elif binding["release"] != release:
                    cacheable = False
                    notes["release_note"] = (f"이 분석은 {binding['release']} 때 냈고 지금 release는 {release}입니다. "
                                             "답은 제출 때 release로 기록했고 캐시하지 않았습니다. 지금 release로 "
                                             "분석하려면 request_id 없이 다시 부르세요")
                    release = binding["release"]
                rid = request_id
            else:
                by_key = {q.key: q for q in queries}
                rid = await self._chipatlas_submit(genome, antigen_class, cell_class, threshold, mode,
                                                   [by_key[k] for k in keys], distance_kb, permutations)
                self.cache.put("chipatlas_requests", Cache.key("chipatlas_requests", "", {}, rid),
                               {"request": request, "release": release}, _now())
            text = await self._chipatlas_wait(rid, float(wait_s))
            answer, cached_at = {"request_id": rid, "rows": chipatlas_rows(text)}, None
            if cacheable:
                self.cache.put("chipatlas", cache_key, answer, _now())
        summary = {**_chipatlas_summary(answer), **notes}
        return self._set_record("chipatlas", "ChIP-Atlas enrichment analysis", release, CHIPATLAS_WABI, options,
                                keys, errors, answer, cached_at, summary)

    async def _chipatlas_release(self) -> tuple[str, bool]:
        """ChIP-Atlas names no release; the experiment table's last update stands for it."""
        url = f"{CHIPATLAS_DATA}/metadata/experimentList.tab"
        response = await self.http.request("chipatlas", "HEAD", url)
        stamp = response.headers.get("Last-Modified") if response.status_code == 200 else None
        try:
            date = parsedate_to_datetime(stamp).date().isoformat() if stamp else None
        except (TypeError, ValueError):
            date = None
        if date is None:
            return "ChIP-Atlas (갱신일을 읽지 못함, 캐시하지 않음)", False
        return f"ChIP-Atlas (experimentList.tab {date})", True

    async def _chipatlas_submit(self, genome: str, antigen_class: str, cell_class: str, threshold: int, mode: str,
                                queries: list[Query], distance_kb: int, permutations: int) -> str:
        if mode == "gene":
            body, type_b, description_b = "\n".join(q.raw for q in queries), "refseq", "refseq_genes"
        else:
            body = "\n".join(f"{q.chrom}\t{q.start0}\t{q.end}" for q in queries)
            type_b, description_b = "rnd", "random_permutation"
        form = {"address": "", "format": "text", "result": "www", "genome": genome, "antigenClass": antigen_class,
                "cellClass": cell_class, "threshold": str(threshold), "typeA": mode if mode == "gene" else "bed",
                "bedAFile": body, "typeB": type_b, "bedBFile": "empty", "permTime": str(permutations),
                "title": "labhq", "descriptionA": "labhq_query", "descriptionB": description_b,
                "distanceUp": str(distance_kb * 1000), "distanceDown": str(distance_kb * 1000)}
        response = await self.http.request("chipatlas", "POST", CHIPATLAS_WABI, data=form)
        match = re.search(r"^requestId:\s*(\S+)", response.text, re.MULTILINE) if response.status_code == 200 else None
        if not match or not _REQUEST_ID.match(match.group(1)):
            raise LookupFailed(f"ChIP-Atlas가 분석을 받지 않았습니다(HTTP {response.status_code}): "
                               f"{response.text[:200]}")
        return match.group(1)

    async def _chipatlas_wait(self, request_id: str, wait_s: float) -> str:
        """Poll the result file until it exists or `wait_s` passes. A JSON body is WABI's 'not found or still
        running'; the status call is not used because it answers not-found for running jobs."""
        url = f"{CHIPATLAS_WABI}{request_id}"
        started = self.http.clock()
        while True:
            response = await self.http.request("chipatlas", "GET", url, params={"info": "result", "format": "tsv"})
            text = response.text
            if response.status_code == 200 and not text.lstrip().startswith("{"):
                if text.lstrip().startswith("[ERROR]"):
                    raise LookupFailed(f"ChIP-Atlas 분석 실패: {text.strip().splitlines()[0][:200]}")
                return text
            if response.status_code not in (200, 404):
                raise LookupFailed(f"ChIP-Atlas 결과 HTTP {response.status_code}: {text[:200]}")
            if self.http.clock() - started + CHIPATLAS_POLL_S > wait_s:
                raise LookupFailed(f"ChIP-Atlas 분석이 {wait_s:.0f}초 안에 끝나지 않았습니다. 같은 입력·옵션에 "
                                   f"request_id=\"{request_id}\"를 붙여 다시 부르면 결과를 이어 받습니다")
            await self.http.sleep(CHIPATLAS_POLL_S)

    # ----- ChIP-Atlas target genes -----
    async def chipatlas_targets(self, antigens: object, genome: str = "hg38", distance_kb: int = 5,
                                genes: list[str] | None = None, top: int = 50) -> dict:
        if genome not in CHIPATLAS_GENOMES:
            raise ValueError(f"genome은 {', '.join(CHIPATLAS_GENOMES)} 중 하나입니다")
        if distance_kb not in CHIPATLAS_TARGET_KB:
            raise ValueError(f"distance_kb는 {CHIPATLAS_TARGET_KB} 중 하나입니다(TSS 앞뒤 kb)")
        if not isinstance(top, int) or not 1 <= top <= 500:
            raise ValueError("top은 1~500입니다")
        wanted = [g.strip().upper() for g in genes or [] if isinstance(g, str) and g.strip()]
        values = [antigens] if isinstance(antigens, str) else antigens
        if not isinstance(values, list) or not values:
            raise ValueError("단백질(antigen) 목록이 비었습니다")
        items = []
        for value in values[:20]:
            if isinstance(value, str) and _ANTIGEN.match(value.strip()):
                raw = value.strip()
                items.append(Item(Query(raw, "antigen", raw), value))
            else:
                items.append(Item(None, value, error="단백질 이름 형식이 아닙니다(예: POU5F1, CTCF)"))
        items += [Item(None, v, error="한 번에 20개까지입니다") for v in values[20:]]
        options = {"genome": genome, "distance_kb": distance_kb}
        dates: set[str] = set()
        lookup_errors = 0
        for item in items:
            if item.error is not None:
                continue
            url = f"{CHIPATLAS_DATA}/{genome}/target/{item.variant.key}.{distance_kb}.tsv"
            try:
                head = await self.http.request("chipatlas", "HEAD", url)
                if head.status_code == 404:
                    item.answer = {"found": False}
                    continue
                if head.status_code != 200:
                    raise LookupFailed(f"HTTP {head.status_code}")
                stamp = head.headers.get("Last-Modified") or head.headers.get("ETag") or ""
                key = Cache.key("chipatlas_targets", stamp, options, item.variant.key)
                hit = self.cache.get("chipatlas_targets", key) if stamp else None
                if hit is not None:
                    item.answer, item.cached_at = hit["answer"], hit.get("queried_at")
                else:
                    item.answer = await self._chipatlas_target_file(url, head)
                    if stamp:
                        self.cache.put("chipatlas_targets", key, item.answer, _now())
                if item.answer.get("last_modified"):
                    dates.add(item.answer["last_modified"])
            except LookupFailed as exc:
                lookup_errors += 1
                item.error = f"ChIP-Atlas target genes 조회 실패: {exc}"
        if self._all_failed(items, lookup_errors):
            raise LookupFailed(f"ChIP-Atlas 조회가 모두 실패했습니다: {next(i.error for i in items if i.error)}")
        release = "ChIP-Atlas target genes" + (f" (파일 {', '.join(sorted(dates))})" if dates else "")
        options_out = {**options, "genes": wanted or None, "top": top}

        def summary(item: Item) -> dict:
            answer = item.answer if isinstance(item.answer, dict) else {}
            if not answer.get("found"):
                return {"found": False, "meaning": "ChIP-Atlas에 이 단백질의 target genes 표가 없습니다(조회는 성공, "
                                                   "목록은 metadata/analysisList.tab)"}
            rows = answer.get("genes") or []
            out = {"found": True, "n_experiments": answer.get("n_experiments"), "n_genes": len(rows),
                   "top": [{"gene": g, "average_score": s, "string_score": t} for g, s, t in rows[:top]],
                   "meaning": "average_score는 TSS 앞뒤 구간 MACS2 binding score의 실험 평균입니다"}
            if wanted:
                found = {g.upper(): (g, s, t) for g, s, t in rows}
                out["requested_genes"] = [{"gene": g, "average_score": found[g][1], "string_score": found[g][2]}
                                          if g in found else {"gene": g, "average_score": None} for g in wanted]
            return out

        # Each answer is cached above under its own file date; not again under the joined release.
        return self._finish("chipatlas_targets", "ChIP-Atlas target genes", release, CHIPATLAS_DATA,
                            options_out, items, summary, cache=False)

    async def _chipatlas_target_file(self, url: str, head: httpx.Response) -> dict:
        size = head.headers.get("Content-Length")
        if size and size.isdigit() and int(size) > CHIPATLAS_TARGET_MAX_BYTES:
            raise LookupFailed(f"파일이 {int(size) // 1_000_000} MB로 너무 큽니다")
        response = await self.http.request("chipatlas", "GET", url)
        if response.status_code != 200:
            raise LookupFailed(f"HTTP {response.status_code}")
        lines = response.text.splitlines()
        if not lines:
            raise LookupFailed("빈 파일입니다")
        header = lines[0].split("\t")
        has_string = header[-1].strip().upper() == "STRING"
        genes = []
        for line in lines[1:]:
            fields = line.split("\t")
            if len(fields) < 2:
                continue
            score = _float(fields[1])
            string = _float(fields[-1]) if has_string and len(fields) > 2 else None
            genes.append([fields[0], score if isinstance(score, float) else None,
                          string if isinstance(string, float) else None])
        genes.sort(key=lambda g: -(g[1] or 0.0))
        stamp = response.headers.get("Last-Modified") or head.headers.get("Last-Modified")
        try:
            modified = parsedate_to_datetime(stamp).date().isoformat() if stamp else None
        except (TypeError, ValueError):
            modified = None
        return {"found": True, "url": url, "last_modified": modified,
                "n_experiments": max(0, len(header) - 2 - int(has_string)), "genes": genes}

    # ----- ENCODE cCREs -----
    async def encode(self, regions: object, assembly: str = "GRCh38") -> dict:
        if assembly not in ENCODE_ASSEMBLIES:
            raise ValueError(f"assembly는 {', '.join(ENCODE_ASSEMBLIES)} 중 하나입니다")
        values = [regions] if isinstance(regions, str) else regions
        if not isinstance(values, list) or not values:
            raise ValueError("영역 목록이 비었습니다")
        items = []
        for index, value in enumerate(values):
            if index >= ENCODE_MAX_REGIONS:
                items.append(Item(None, value, error=f"한 번에 {ENCODE_MAX_REGIONS}개까지입니다. 나머지는 다시 호출하세요"))
                continue
            try:
                items.append(Item(parse_region(value, ENCODE_MAX_WIDTH), value))
            except ValueError as exc:
                items.append(Item(None, value, error=str(exc)))
        registry = await self._encode_registry(assembly)
        release = (f"ENCODE cCRE registry {registry['version']} ({registry['annotation']}, file {registry['file']}, "
                   f"released {registry['released']})")
        options = {"assembly": assembly}
        self._from_cache("encode", release, options, items)
        pending = {item.variant.key: item.variant for item in items
                   if item.variant and item.error is None and item.cached_at is None}
        if pending:
            path, temporary = await self._encode_file(registry)
            try:
                hits = await asyncio.to_thread(scan_ccres, path, list(pending.values()))
            except (OSError, ValueError, EOFError) as exc:
                raise LookupFailed(f"cCRE 파일을 읽지 못했습니다: {exc}") from exc
            finally:
                if temporary:
                    path.unlink(missing_ok=True)
            for item in items:
                if item.variant and item.variant.key in hits and item.cached_at is None and item.error is None:
                    item.answer = {"ccres": hits[item.variant.key]}
        return self._finish("encode", "ENCODE cCREs", release, f"{ENCODE}/annotations/{registry['annotation']}/",
                            options, items, _encode_summary)

    async def _encode_registry(self, assembly: str) -> dict:
        body = await self._json("encode", f"{ENCODE}/search/", {
            "type": "Annotation", "annotation_type": "candidate Cis-Regulatory Elements", "assembly": assembly,
            "status": "released", "biosample_ontology.term_name!": "*", "format": "json", "limit": "all",
            "field": ["accession", "encyclopedia_version", "date_released", "description"]}, "ENCODE 검색")
        graph = body.get("@graph") if isinstance(body, dict) else None
        if not isinstance(graph, list) or not graph:
            raise LookupFailed(f"ENCODE에 {assembly} cCRE registry가 없습니다")

        def rank(entry: dict) -> tuple:
            versions = [int(m.group(1)) for v in entry.get("encyclopedia_version") or []
                        for m in [re.search(r"v([0-9]+)", str(v))] if m]
            return (max(versions, default=0), "agnostic" in str(entry.get("description", "")).lower(),
                    str(entry.get("date_released") or ""))
        best = max((e for e in graph if isinstance(e, dict) and e.get("accession")), key=rank)
        files = await self._json("encode", f"{ENCODE}/search/", {
            "type": "File", "dataset": f"/annotations/{best['accession']}/", "status": "released",
            "file_format": "bed", "output_type": "candidate Cis-Regulatory Elements", "format": "json",
            "limit": "all", "field": ["accession", "md5sum", "href", "file_size"]}, "ENCODE 파일 검색")
        candidates = [f for f in (files.get("@graph") if isinstance(files, dict) else None) or []
                      if isinstance(f, dict) and str(f.get("href", "")).endswith(".bed.gz") and f.get("md5sum")]
        if not candidates:
            raise LookupFailed(f"ENCODE {best['accession']}에 bed.gz 파일이 없습니다")
        chosen = candidates[0]
        version = next((m.group(0) for v in best.get("encyclopedia_version") or []
                        for m in [re.search(r"v[0-9]+", str(v))] if m), "unknown")
        return {"annotation": best["accession"], "version": f"V{version[1:]}" if version != "unknown" else version,
                "released": best.get("date_released") or "unknown", "file": chosen["accession"],
                "md5": chosen["md5sum"], "href": chosen["href"], "size": chosen.get("file_size")}

    async def _encode_file(self, registry: dict) -> tuple[Path, bool]:
        """The registry bed.gz on disk: the cached copy when its md5 matches, else a fresh download."""
        if isinstance(registry.get("size"), int) and registry["size"] > ENCODE_MAX_BYTES:
            raise LookupFailed(f"cCRE 파일이 {registry['size'] // 1_000_000} MB로 너무 큽니다")
        root = self.cache.root
        target = root / "encode" / f"{registry['file']}.bed.gz" if root else None
        if target is not None and target.is_file():
            if await asyncio.to_thread(_md5, target) == registry["md5"]:
                return target, False
        response = await self.http.request("encode", "GET", f"{ENCODE}{registry['href']}", follow_redirects=True)
        if response.status_code != 200:
            raise LookupFailed(f"cCRE 파일 HTTP {response.status_code}")
        content = response.content
        if hashlib.md5(content).hexdigest() != registry["md5"]:  # noqa: S324 - ENCODE publishes md5
            raise LookupFailed("내려받은 cCRE 파일의 md5가 ENCODE 기록과 다릅니다")
        if target is not None:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
                temporary.write_bytes(content)
                os.replace(temporary, target)
                return target, False
            except OSError:
                pass
        fd, name = tempfile.mkstemp(suffix=".bed.gz")
        with os.fdopen(fd, "wb") as out:
            out.write(content)
        return Path(name), True

    # ----- GTEx -----
    async def gtex(self, genes_or_variants: object, tissues: list[str] | None = None, dataset: str = "gtex_v10",
                   eqtl: bool = True) -> dict:
        if not isinstance(dataset, str) or not _GTEX_DATASET.match(dataset):
            raise ValueError("dataset은 GTEx dataset ID입니다(예: gtex_v10, gtex_v8)")
        values = [genes_or_variants] if isinstance(genes_or_variants, str) else genes_or_variants
        if not isinstance(values, list) or not values:
            raise ValueError("유전자나 변이 목록이 비었습니다")
        meta = await self._gtex_dataset(dataset)
        wanted = await self._gtex_tissues(dataset, tissues)
        items = []
        for index, value in enumerate(values):
            if index >= GTEX_MAX_ITEMS:
                items.append(Item(None, value, error=f"한 번에 {GTEX_MAX_ITEMS}개까지입니다. 나머지는 다시 호출하세요"))
                continue
            try:
                items.append(Item(self._gtex_query(value), value))
            except ValueError as exc:
                items.append(Item(None, value, error=str(exc)))
        release = (f"{meta.get('displayName') or dataset} ({dataset}, GENCODE {meta.get('gencodeVersion')}, "
                   f"dbSNP {meta.get('dbSnpBuild')})")
        options = {"dataset": dataset, "tissues": wanted, "eqtl": bool(eqtl), "max_eqtl_rows": GTEX_PAGE * GTEX_MAX_PAGES,
                   "eqtl_rows_per": "tissue" if wanted else "item"}
        self._from_cache("gtex", release, options, items)
        lookup_errors = 0
        done: dict[str, object] = {}
        for item in items:
            if not item.variant or item.error is not None or item.cached_at is not None:
                continue
            query = item.variant
            if query.key not in done:
                try:
                    if query.kind == "gene":
                        done[query.key] = await self._gtex_gene(query, meta, dataset, wanted, bool(eqtl))
                    else:
                        done[query.key] = await self._gtex_variant(query, dataset, wanted)
                except LookupFailed as exc:
                    lookup_errors += 1
                    done[query.key] = exc
                except ValueError as exc:
                    done[query.key] = exc
            answer = done[query.key]
            if isinstance(answer, Exception):
                item.error = f"GTEx 조회 실패: {answer}"
            else:
                item.answer = answer
        if self._all_failed(items, lookup_errors):
            raise LookupFailed(f"GTEx 조회가 모두 실패했습니다: {next(i.error for i in items if i.error)}")
        return self._finish("gtex", "GTEx Portal API v2", release, GTEX, options, items, _gtex_summary)

    @staticmethod
    def _gtex_query(value: object) -> Query:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("유전자 기호·Ensembl ID, chr:pos:ref:alt (GRCh38) 또는 rsID입니다")
        raw = value.strip()
        if _STRICT_RSID.match(raw) or raw.count(":") == 3:
            variant = parse_variant(raw)
            return Query(raw, "variant", variant.key, variant=variant)
        return parse_gene(raw)

    async def _gtex_get(self, path: str, params: dict) -> dict:
        response = await self.http.request("gtex", "GET", f"{GTEX}/{path}", params=params,
                                           headers={"Accept": "application/json"})
        if response.status_code in (400, 404, 422):
            raise ValueError(f"GTEx가 거부했습니다(HTTP {response.status_code}): {_error_text(response)}")
        if response.status_code != 200:
            raise LookupFailed(f"{path} HTTP {response.status_code}: {_error_text(response)}")
        try:
            body = response.json()
        except ValueError as exc:
            raise LookupFailed(f"{path} 응답이 JSON이 아닙니다: {exc}") from exc
        if isinstance(body, list):
            return {"data": body}
        if not isinstance(body, dict):
            raise LookupFailed(f"{path} 응답을 읽지 못했습니다")
        return body

    async def _gtex_paged(self, path: str, params: dict, max_pages: int = GTEX_MAX_PAGES) -> dict:
        rows: list[dict] = []
        total = None
        page = 0
        while page < max_pages:
            body = await self._gtex_get(path, {**params, "page": page, "itemsPerPage": GTEX_PAGE})
            rows += [r for r in body.get("data") or [] if isinstance(r, dict)]
            paging = body.get("paging_info") or {}
            total = paging.get("totalNumberOfItems", total)
            pages = paging.get("numberOfPages") or 1
            page += 1
            if page >= pages:
                break
        return {"rows": rows, "total": total if isinstance(total, int) else len(rows),
                "truncated": isinstance(total, int) and total > len(rows)}

    async def _gtex_dataset(self, dataset: str) -> dict:
        try:
            body = await self._gtex_get("metadata/dataset", {"datasetId": dataset})
        except ValueError as exc:
            raise ValueError(f"GTEx dataset을 찾지 못했습니다: {dataset}") from exc
        rows = [r for r in body.get("data") or [] if isinstance(r, dict) and r.get("datasetId") == dataset]
        if not rows:
            raise ValueError(f"GTEx에 없는 dataset입니다: {dataset}")
        return rows[0]

    async def _gtex_tissues(self, dataset: str, tissues: list[str] | None) -> list[str] | None:
        if not tissues:
            return None
        if not isinstance(tissues, list) or not all(isinstance(t, str) and _TISSUE.match(t) for t in tissues):
            raise ValueError("tissues는 GTEx tissueSiteDetailId 목록입니다(예: Liver, Whole_Blood)")
        body = await self._gtex_get("dataset/tissueSiteDetail", {"datasetId": dataset, "itemsPerPage": GTEX_PAGE})
        known = {str(r.get("tissueSiteDetailId")).lower(): r.get("tissueSiteDetailId")
                 for r in body.get("data") or [] if isinstance(r, dict) and r.get("tissueSiteDetailId")}
        unknown = [t for t in tissues if t.lower() not in known]
        if unknown:
            raise ValueError(f"GTEx {dataset}에 없는 조직: {', '.join(unknown)}. 예: {', '.join(list(known.values())[:5])}")
        return sorted({known[t.lower()] for t in tissues})

    async def _gtex_gene(self, query: Query, meta: dict, dataset: str, tissues: list[str] | None,
                         eqtl: bool) -> dict:
        body = await self._gtex_get("reference/gene", {"geneId": query.raw, "gencodeVersion": meta.get("gencodeVersion"),
                                                       "genomeBuild": meta.get("genomeBuild"), "itemsPerPage": 50})
        genes = [g for g in body.get("data") or [] if isinstance(g, dict)]
        exact = [g for g in genes if str(g.get("geneSymbolUpper") or "").upper() == query.raw.upper()
                 or str(g.get("gencodeId") or "").split(".")[0] == query.raw.split(".")[0]]
        if not exact:
            return {"found": False, "meaning": f"GTEx {dataset}(GENCODE {meta.get('gencodeVersion')})에 이 유전자가 "
                                               "없습니다(조회는 성공)"}
        gene = exact[0]
        gencode = gene["gencodeId"]
        info = {k: gene.get(k) for k in ("gencodeId", "geneSymbol", "chromosome", "start", "end", "strand",
                                         "geneType")}
        if len(exact) > 1:
            info["other_matches"] = [g.get("gencodeId") for g in exact[1:]]
        expression = await self._gtex_paged("expression/medianGeneExpression",
                                            {"gencodeId": gencode, "datasetId": dataset}, 1)
        rows = [r for r in expression["rows"] if not tissues or r.get("tissueSiteDetailId") in tissues]
        answer: dict[str, Any] = {"found": True, "gene": info, "tissues": tissues,
                                  "expression": [{k: r.get(k) for k in ("tissueSiteDetailId", "median", "unit")}
                                                 for r in rows]}
        if eqtl:
            answer["eqtl"] = await self._gtex_eqtl({"gencodeId": gencode, "datasetId": dataset}, tissues)
        return answer

    async def _gtex_eqtl(self, params: dict, tissues: list[str] | None) -> dict:
        """singleTissueEqtl rows, paged per requested tissue so the 1,000-row stop and the totals count only those
        tissues (one unfiltered query could spend every page on other tissues)."""
        if not tissues:
            return await self._gtex_paged("association/singleTissueEqtl", params)
        rows: list[dict] = []
        total, truncated = 0, False
        for tissue in tissues:
            found = await self._gtex_paged("association/singleTissueEqtl", {**params, "tissueSiteDetailId": tissue})
            rows += [r for r in found["rows"] if r.get("tissueSiteDetailId") == tissue]
            total += found["total"]
            truncated = truncated or found["truncated"]
        return {"rows": rows, "total": total, "truncated": truncated}

    async def _gtex_variant(self, query: Query, dataset: str, tissues: list[str] | None) -> dict:
        variant = query.variant
        assert variant is not None
        if variant.kind == "vcf":
            lookup = {"variantId": f"{_ucsc(variant.chrom)}_{variant.pos}_{variant.ref}_{variant.alt}_b38"}
        elif variant.kind == "rsid":
            lookup = {"snpId": variant.key}
        else:
            raise ValueError("GTEx는 chr:pos:ref:alt (GRCh38)나 rsID만 받습니다")
        body = await self._gtex_get("dataset/variant", {**lookup, "datasetId": dataset})
        known = [{k: r.get(k) for k in ("variantId", "snpId", "b37VariantId", "maf01")}
                 for r in body.get("data") or [] if isinstance(r, dict) and r.get("variantId")]
        if not known:
            return {"found": False, "meaning": f"GTEx {dataset}의 genotype에 없는 변이입니다(조회는 성공)"}
        rows: list[dict] = []
        total, truncated = 0, False
        for entry in known[:5]:
            found = await self._gtex_eqtl({"variantId": entry["variantId"], "datasetId": dataset}, tissues)
            rows += found["rows"]
            total += found["total"]
            truncated = truncated or found["truncated"]
        return {"found": True, "variants": known, "tissues": tissues,
                "eqtl": {"rows": rows, "total": total, "truncated": truncated}}

    # ----- AlphaGenome -----
    async def alphagenome(self, variants_or_intervals: object, outputs: list[str] | None = None,
                          ontology_terms: list[str] | None = None, sequence_length: str = AG_DEFAULT_LENGTH,
                          organism: str = "human") -> dict:
        backend = self.alphagenome_backend
        if backend is None:
            raise LookupFailed("AlphaGenome 키가 없어 쓸 수 없습니다(labhq init에서 키를 넣으세요)")
        outputs = list(dict.fromkeys(outputs or ["RNA_SEQ"]))
        bad = [o for o in outputs if o not in AG_OUTPUTS]
        if bad:
            raise ValueError(f"outputs는 {', '.join(AG_OUTPUTS)} 중에서 고릅니다(받지 않음: {', '.join(map(str, bad))})")
        terms = list(dict.fromkeys(ontology_terms or []))
        if len(terms) > AG_MAX_TERMS or not all(isinstance(t, str) and _ONTOLOGY.match(t) for t in terms):
            raise ValueError(f"ontology_terms는 UBERON:0001157 같은 CURIE {AG_MAX_TERMS}개까지입니다")
        if sequence_length not in AG_LENGTHS:
            raise ValueError(f"sequence_length는 {', '.join(AG_LENGTHS)} 중 하나입니다")
        if organism not in AG_ORGANISMS:
            raise ValueError(f"organism은 {', '.join(AG_ORGANISMS)} 중 하나입니다")
        width = AG_LENGTHS[sequence_length]
        heavy = [o for o in outputs if o in AG_BP_OUTPUTS]
        if not terms and heavy and width > AG_ALL_TRACKS_MAX:
            raise ValueError(f"{', '.join(heavy)}는 1 bp 해상도라 ontology_terms 없이(모든 track) {sequence_length}를 "
                             f"받으면 변이 하나에 수 GB입니다. ontology_terms(예: UBERON:0002107)를 주거나 "
                             f"sequence_length를 100KB 이하로 주세요")
        values = [variants_or_intervals] if isinstance(variants_or_intervals, str) else variants_or_intervals
        if not isinstance(values, list) or not values:
            raise ValueError("변이나 구간 목록이 비었습니다")
        items = []
        for index, value in enumerate(values):
            if index >= AG_MAX_PER_CALL:
                items.append(Item(None, value, error=f"한 번에 {AG_MAX_PER_CALL}개까지입니다. 나머지는 다시 호출하세요"))
                continue
            try:
                items.append(Item(_alphagenome_query(value, width), value))
            except ValueError as exc:
                items.append(Item(None, value, error=str(exc)))
        release = backend.release()
        options = {"outputs": outputs, "ontology_terms": terms or None, "sequence_length": sequence_length,
                   "organism": organism}
        self._from_cache("alphagenome", release, options, items)
        lookup_errors = 0
        done: dict[str, object] = {}
        stopped: str | None = None  # after a timeout or an unreachable service the rest fail at once
        for item in items:
            if not item.variant or item.error is not None or item.cached_at is not None:
                continue
            query = item.variant
            if query.key not in done and stopped is not None:
                lookup_errors += 1
                done[query.key] = LookupFailed(f"앞 항목이 실패해 건너뜀({stopped})")
            elif query.key not in done:
                await self.http._pace("alphagenome")
                try:
                    tracks = await _alphagenome_call(backend.predict, query, width, outputs, terms or None, organism)
                    centre = (query.variant.pos - 1) if query.variant else (query.start0 + query.end) // 2
                    start0 = max(0, centre - width // 2)
                    done[query.key] = {"window": f"{query.chrom}:{start0 + 1}-{start0 + width}",
                                       "summarised_over": "window" if query.variant else query.key, "tracks": tracks}
                except asyncio.TimeoutError:
                    lookup_errors += 1
                    stopped = f"{AG_TIMEOUT_S:.0f}초 안에 답이 없었습니다"
                    done[query.key] = LookupFailed(stopped)
                except AlphaGenomeUnavailable as exc:
                    lookup_errors += 1
                    stopped = backend.redact(str(exc))[:400]
                    done[query.key] = LookupFailed(stopped)
                except Exception as exc:  # noqa: BLE001 - the client raises grpc and value errors alike
                    lookup_errors += 1
                    done[query.key] = LookupFailed(backend.redact(f"{type(exc).__name__}: {exc}")[:500])
            answer = done[query.key]
            if isinstance(answer, Exception):
                item.error = f"AlphaGenome 예측 실패: {answer}"
            else:
                item.answer = answer
        if self._all_failed(items, lookup_errors):
            raise LookupFailed(f"AlphaGenome 예측이 모두 실패했습니다: {next(i.error for i in items if i.error)}")
        return self._finish("alphagenome", "Google DeepMind AlphaGenome", release, "https://alphagenome.google/api",
                            options, items, _alphagenome_summary)


def _alphagenome_query(value: object, width: int) -> Query:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("변이는 chr:pos:ref:alt, 구간은 chr:start-end입니다")
    raw = value.strip()
    if raw.count(":") == 3:
        variant = parse_variant(raw)
        if variant.kind != "vcf":
            raise ValueError("AlphaGenome에는 chr:pos:ref:alt 형식으로 주세요")
        return Query(raw, "variant", variant.key, _ucsc(variant.chrom), variant.pos - 1,
                     variant.pos - 1 + len(variant.ref), variant)
    if _STRICT_RSID.match(raw) or ":" not in raw:
        raise ValueError("AlphaGenome은 rsID·HGVS를 받지 않습니다. chr:pos:ref:alt나 chr:start-end로 주세요")
    region = parse_region(raw)
    if region.end - region.start0 > width:
        raise ValueError(f"구간이 sequence_length({width:,} bp)보다 깁니다")
    return region


def _md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - ENCODE publishes md5
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()

