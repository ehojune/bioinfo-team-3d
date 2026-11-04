"""Bounded AlphaGenome v1 variant scoring; SDK imports stay optional.

Contract: official RECOMMENDED_VARIANT_SCORERS and tidy_scores, checked 2026-10-15.
See docs/reference/alphagenome-variant.md for sources and interpretation limits.
"""

from __future__ import annotations

import asyncio
import heapq
import importlib.metadata
import math
from numbers import Real
from typing import Any

from .annot import Item, LookupFailed
from . import annot_regulatory as reg

MAX_SAVED_ROWS = 1000
MAX_TOP = 100
MAX_ALLELE = 1000
# Part of the cache key. 2: the window is centered on the REF span (#621); scores cached with the older, lopsided window
# of a many-base REF are not reused.
SCORE_SCHEMA = 2
MAX_SCORERS = 20
LARGE_QUANTILE = 0.99  # display convention, not a clinical or significance cutoff
SOURCE = {
    "api_version": "v1", "model_version": "ALL_FOLDS",
    "server_build": "not exposed by SDK",
    "sdk_source": "https://github.com/google-deepmind/alphagenome",
    "scoring_docs": "https://www.alphagenomedocs.com/variant_scoring.html",
    "terms_url": "https://alphagenome.google/terms",
    "usage": "비상업 연구용 API 출력. 다른 ML 모델 학습에 사용 금지. 예측이며 임상 근거 아님.",
}


def _setup(backend: reg.AlphaGenomeBackend) -> tuple[Any, dict, dict]:
    module = getattr(backend, "_variant_scorers", None)
    if module is None:
        from alphagenome.models import variant_scorers as module
        backend._variant_scorers = module
    # Active-allele scores measure activity, not ALT-REF effects; keep only differential scorers.
    scorers = {k: v for k, v in module.RECOMMENDED_VARIANT_SCORERS.items() if not k.endswith("_ACTIVE")}
    if not scorers or len(scorers) > MAX_SCORERS:
        raise LookupFailed("AlphaGenome 권장 scorer 목록이 비었거나 20개 상한을 넘었습니다")
    version = backend._version
    if version is None:
        version = importlib.metadata.version("alphagenome")
    descriptors = [{"name": name, "config": str(scorer), "is_signed": getattr(scorer, "is_signed", None)}
                   for name, scorer in sorted(scorers.items())]
    return module, scorers, {**SOURCE, "sdk_version": version, "scorers": descriptors}


def _scalar(value: Any, backend: reg.AlphaGenomeBackend) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, Real):
        return float(value) if math.isfinite(value) else None
    text = str(value)
    return None if text in ("<NA>", "NaT") else backend.redact(text)


def _rank(row: dict) -> float:
    score = row.get("quantile_score")
    return abs(score) if isinstance(score, (int, float)) and -1 <= score <= 1 else -1.0


def _score(backend: reg.AlphaGenomeBackend, query: reg.Query, width: int, module: Any,
           scorers: dict, terms: list[str], tissues: list[str]) -> dict:
    dna, genome = backend._modules()
    model = getattr(backend, "_score_model", None)
    if model is None:
        try:
            model = dna.create(backend._key, model_version=dna.ModelVersion.ALL_FOLDS,
                               timeout=reg.AG_CONNECT_TIMEOUT_S)
        except Exception:
            raise reg.AlphaGenomeUnavailable("AlphaGenome 연결 실패(키·연결 상태 확인)") from None
        backend._score_model = model
    variant = query.variant
    # Centered on the middle of the REF span, as AlphaGenome's `reference_interval.resize(width)` is (#621): a deletion of
    # many bases is not left lopsided in its window. For a one-base REF the middle is the variant's own base, as before.
    start = max(0, (query.start0 + query.end) // 2 - width // 2)
    interval = genome.Interval(chromosome=query.chrom, start=start, end=start + width)
    allele = genome.Variant(chromosome=query.chrom, position=variant.pos,
                            reference_bases=variant.ref, alternate_bases=variant.alt)
    # score_variant has no ontology_terms parameter. Filter tidy metadata locally after scoring.
    scores = model.score_variant(interval=interval, variant=allele, variant_scorers=list(scorers.values()),
                                 organism=dna.Organism.HOMO_SAPIENS)
    frame = module.tidy_scores([scores])  # nested batches supported by both old and current SDKs
    directions = {str(s): getattr(s, "is_signed", None) for s in scorers.values()}
    best: list[tuple[float, int, dict]] = []
    total = selected = 0
    if frame is not None:
        for index, values in enumerate(frame.itertuples(index=False, name=None)):
            total += 1
            row = {backend.redact(str(k)): _scalar(v, backend) for k, v in zip(frame.columns, values)}
            if terms and row.get("ontology_curie") not in terms:
                continue
            if tissues and not any(str(row.get(k) or "").casefold() in tissues
                                   for k in ("biosample_name", "gtex_tissue")):
                continue
            selected += 1
            row["is_signed"] = directions.get(row.get("variant_scorer"))
            entry = (_rank(row), -index, row)
            if len(best) < MAX_SAVED_ROWS:
                heapq.heappush(best, entry)
            elif entry[:2] > best[0][:2]:
                heapq.heapreplace(best, entry)
    return {"window": f"{query.chrom}:{start+1}-{start+width}", "n_rows_before_filter": total,
            "n_rows": selected, "rows": [r for _, _, r in sorted(best, reverse=True)]}


def _summary(item: Item, top: int) -> dict:
    answer = item.answer
    rows = answer["rows"]
    if not rows:
        text = "선택한 조건에 점수 행 없음. 효과가 없다는 뜻은 아닙니다."
    else:
        row = rows[0]
        raw = row.get("raw_score")
        if row.get("is_signed") is True and isinstance(raw, (int, float)):
            direction = "증가" if raw > 0 else "감소" if raw < 0 else "변화 없음"
        else:
            direction = "방향 미제공(절대 차이 또는 부호 정보 없음)"
        tissue = row.get("biosample_name") or row.get("gtex_tissue") or row.get("ontology_curie") or "조직 정보 없음"
        gene = row.get("gene_name") or row.get("gene_id") or "유전자 지정 없음"
        rank = _rank(row)
        if rank < 0:
            effect = "quantile 없음: 큰 효과 판정 보류, modality 간 순위도 보류"
        else:
            effect = ("큰 효과 후보" if rank >= LARGE_QUANTILE else "큰 효과 기준 미만")
            effect += f" (|quantile|={rank:.4g}, 표시 기준 {LARGE_QUANTILE})"
        lead = "|quantile|이 가장 큰 행" if rank >= 0 else "첫 점수 행"
        text = f"{lead}: {row.get('output_type')} · {tissue} · {gene}: {direction}. {effect}."
    return {"variant": item.variant.key, "window": answer["window"], "summary_ko": text,
            "n_rows_before_filter": answer["n_rows_before_filter"], "n_rows": answer["n_rows"],
            "n_saved": len(rows), "truncated": answer["n_rows"] > len(rows), "top": rows[:top]}


async def score_variants(annotator: reg.RegulatoryAnnotator, variants: object, *,
                         sequence_length: str = "1MB", ontology_terms: list[str] | None = None,
                         tissues: list[str] | None = None, top: int = 20, assembly: str = "GRCh38") -> dict:
    backend = annotator.alphagenome_backend
    if backend is None:
        raise LookupFailed("AlphaGenome 키 없음 또는 SDK 미설치. labhq doctor 안내를 확인하세요")
    if assembly != "GRCh38":
        raise ValueError("alphagenome_variant는 GRCh38 변이만 받습니다")
    if sequence_length not in reg.AG_LENGTHS:
        raise ValueError("sequence_length는 16KB·100KB·500KB·1MB(상한) 중 하나입니다")
    if type(top) is not int or not 1 <= top <= MAX_TOP:
        raise ValueError("top은 1~100입니다")
    terms = ontology_terms or []
    tissue_names = tissues or []
    if not isinstance(terms, list) or len(terms) > reg.AG_MAX_TERMS or not all(
            isinstance(t, str) and reg._ONTOLOGY.fullmatch(t) for t in terms):
        raise ValueError("ontology_terms는 CURIE 목록, 최대 20개입니다")
    if not isinstance(tissue_names, list) or len(tissue_names) > reg.AG_MAX_TERMS or not all(
            isinstance(t, str) and 1 <= len(t.strip()) <= 100 for t in tissue_names):
        raise ValueError("tissues는 조직/세포 이름 목록, 각 100자·최대 20개입니다")
    terms = sorted(set(terms))
    tissue_names = sorted({t.strip().casefold() for t in tissue_names})
    values = [variants] if isinstance(variants, str) else variants
    if not isinstance(values, list) or not values:
        raise ValueError("변이 목록이 비었습니다")
    width = reg.AG_LENGTHS[sequence_length]
    items = []
    for index, raw in enumerate(values):
        # Never echo unvalidated arbitrary input (including an accidentally pasted credential).
        safe_raw = backend.redact(raw) if isinstance(raw, str) and len(raw) <= 2100 else "[invalid input]"
        try:
            if index >= reg.AG_MAX_PER_CALL:
                raise ValueError("한 번에 10개까지입니다. 나머지는 다시 호출하세요")
            if not isinstance(raw, str) or len(raw) > 2100:
                raise ValueError("변이는 chr:pos:ref>alt 또는 chr:pos:ref:alt입니다")
            query = reg._alphagenome_query(raw.replace(">", ":"), width)
            if query.variant is None or max(len(query.variant.ref), len(query.variant.alt)) > MAX_ALLELE:
                raise ValueError("변이만 받으며 allele 길이는 1,000 bp까지입니다")
            items.append(Item(query, safe_raw))
        except ValueError:
            reason = "한 번에 10개까지입니다" if index >= reg.AG_MAX_PER_CALL else "GRCh38 변이 형식·allele 1,000 bp 상한을 확인하세요"
            items.append(Item(None, safe_raw, error=reason))
    try:
        module, scorers, source = _setup(backend)
    except Exception:
        raise LookupFailed("AlphaGenome SDK의 권장 scorer를 읽지 못했습니다. SDK 설치·버전을 확인하세요") from None
    release = f"AlphaGenome v1 / ALL_FOLDS / SDK {source['sdk_version']}"
    options = {"assembly": assembly, "sequence_length": sequence_length, "ontology_terms": terms,
               "tissues": tissue_names, "provenance": source, "saved_rows_limit": MAX_SAVED_ROWS,
               "score_schema": SCORE_SCHEMA}
    annotator._from_cache("alphagenome_variant", release, options, items)
    done: dict[str, Any] = {}
    stopped = False
    for item in items:
        if item.variant is None or item.error or item.cached_at:
            continue
        key = item.variant.key
        if key not in done:
            if stopped:
                done[key] = "앞 항목 연결 실패·시간 초과로 건너뜀"
            else:
                await annotator.http._pace("alphagenome")
                try:
                    done[key] = await reg._alphagenome_call(_score, backend, item.variant, width, module,
                                                           scorers, terms, tissue_names)
                except (asyncio.TimeoutError, reg.AlphaGenomeUnavailable):
                    stopped = True
                    done[key] = "AlphaGenome 연결 실패 또는 시간 초과"
                except Exception:
                    # SDK/gRPC exception messages may contain credentials. Never forward them.
                    done[key] = "AlphaGenome 점수 조회 실패(SDK·변이·서비스 상태 확인)"
        if isinstance(done[key], str):
            item.error = done[key]
        else:
            item.answer = done[key]
    if any(i.variant for i in items) and all(i.error for i in items):
        raise LookupFailed("AlphaGenome 점수 조회가 모두 실패했습니다")
    note = ("예측이며 임상 근거 아님. |quantile| 내림차순이며 0.99 이상은 큰 효과 후보라는 표시 기준입니다. "
            "signed quantile은 -1~1로 재척도한 값으로 원래 백분위와 다릅니다. p값·병원성 확률이 아닙니다. "
            "quantile 없는 행은 뒤에 두며 크기 비교에서 제외합니다. "
            "조직/ontology는 조회 후 필터이며 API 호출량을 줄이지 않습니다. "
            "원 수치 표는 변이당 상위 1,000행까지 저장합니다. " + SOURCE["usage"])
    return annotator._finish("alphagenome_variant", "Google DeepMind AlphaGenome", release,
                             SOURCE["scoring_docs"], options, items, lambda i: _summary(i, top), note=note,
                             source_details=source)
