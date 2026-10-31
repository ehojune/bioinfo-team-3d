"""Bounded, precomputed Atlas queries. No model-inference fallback or key-file access."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import heapq
import importlib.metadata
import importlib.util
import os
import re
import threading
from typing import Any

from . import annot_regulatory as reg
from .annot import Item, LookupFailed
from .annot_alphagenome import _rank, _scalar

KEY_ENV = "ALPHAGENOME_API_KEY"


def credential(*environments: dict[str, str]) -> str:
    """Resolve the runner/engine/task credential in order, including Windows name casing."""
    value = ""
    for env in environments:
        for name, candidate in env.items():
            if name.casefold() == KEY_ENV.casefold():
                value = candidate.strip()
    return value


def credentials(*environments: dict[str, str]) -> set[str]:
    # Remove shadowed values and aliases too; an empty engine override must not expose the parent's key.
    return {value.strip() for env in environments for name, value in env.items()
            if name.casefold() == KEY_ENV.casefold() and value.strip()}


def without_credentials(env: dict[str, str], secrets: set[str]) -> dict[str, str]:
    return {name: value for name, value in env.items()
            if name.casefold() != KEY_ENV.casefold() and not any(key in value for key in secrets)}


MAX_SCORERS = 10
MAX_ROWS = 1000
MAX_CELLS = 1_000_000
SOURCE = {
    "kind": "precomputed_atlas",
    "dataset_release": "not exposed by Atlas SDK",
    "url": "https://alphagenome.google/atlas",
    "sdk_source": "https://github.com/google-deepmind/alphagenome",
    "citation": "Cheng et al. 2026, doi:10.64898/2026.09.16.26363192",
    "terms_url": "https://alphagenome.google/terms",
    "usage": "Atlas API 비상업 연구용. 임상 결정·다른 ML 모델 학습에 사용 금지. 별도 허용 artifact 조건은 약관 참조.",
}


def sdk_available() -> bool:
    try:
        return importlib.util.find_spec("alphagenome.atlas.atlas") is not None
    except (ImportError, ValueError):
        return False


class AtlasBackend:
    def __init__(self, key: str, *, atlas: Any = None, genome: Any = None, version: str | None = None):
        self._key = key
        self._atlas, self._genome, self.version = atlas, genome, version
        self._client = None
        self._metadata = None
        self.cancelled = threading.Event()

    @classmethod
    def from_env(cls) -> AtlasBackend | None:
        key = os.environ.get(KEY_ENV, "").strip()
        return cls(key) if key else None

    def redact(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text

    def setup(self) -> str:
        try:
            if self._atlas is None:
                from alphagenome.atlas import atlas
                from alphagenome.data import genome
                self._atlas, self._genome = atlas, genome
            if not callable(self._atlas.create) or not callable(self._atlas.AtlasClient.query_variant):
                raise ImportError
            return self.version or importlib.metadata.version("alphagenome")
        except Exception:
            raise LookupFailed("Atlas SDK 미지원: alphagenome>=0.10.0의 atlas 모듈을 설치하세요") from None

    def query(self, query: reg.Query, scorers: list[str], terms: list[str], genes: list[str]) -> dict:
        self._check_active()
        if self._client is None:
            self._client = self._atlas.create(self._key, timeout=reg.AG_CONNECT_TIMEOUT_S)
        self._check_active()
        if self._metadata is None:
            self._metadata = self._client.scorer_metadata()
        self._check_active()
        if any(name not in self._metadata for name in scorers):
            raise LookupFailed("Atlas가 지원하지 않는 scorer입니다. 공식 Atlas scorer 목록을 확인하세요")
        variant = query.variant
        allele = self._genome.Variant(chromosome=query.chrom, position=variant.pos,
                                      reference_bases=variant.ref, alternate_bases=variant.alt)
        scores = self._client.query_variant(allele, requested_scorers=scorers,
                                           ontology_terms=terms or None, gene_ids=genes or None)
        best: list[tuple[float, int, dict]] = []
        count = 0
        cells = 0
        for name, data in scores.items():
            if name not in scorers:
                raise LookupFailed("Atlas 응답 scorer가 요청과 다릅니다")
            cells += data.n_obs * data.n_vars
            if cells > MAX_CELLS:
                raise LookupFailed("Atlas 응답이 1,000,000개 점수 상한을 넘었습니다. scorer·조직·유전자를 좁히세요")
            signed = bool(self._metadata[name].is_signed)
            quantiles = data.layers.get("quantiles")
            for i in range(data.n_obs):
                obs = data.obs.iloc[i].to_dict()
                for j in range(data.n_vars):
                    meta = data.var.iloc[j].to_dict()
                    # Server-side filters are also checked before saving returned metadata.
                    # Match the SDK: scorers without ontology metadata are exempt (e.g. aggregate scores).
                    if terms and meta.get("ontology_curie") and meta["ontology_curie"] not in terms:
                        continue
                    if genes and obs.get("gene_id") not in genes:
                        continue
                    # Gene and track metadata have different meanings for fields such as strand.
                    # Preserve both namespaces, including SDK-specific junction_Start/junction_End spelling.
                    row = {"gene_metadata": {self.redact(str(k)): _scalar(v, self) for k, v in obs.items()
                                              if k != "variant"},
                           "track_metadata": {self.redact(str(k)): _scalar(v, self) for k, v in meta.items()},
                           "track_index": j}
                    row.update({k: _scalar(obs[k], self) for k in ("gene_id", "gene_name") if k in obs})
                    row.update({k: _scalar(meta[k], self) for k in ("ontology_curie", "biosample_name", "gtex_tissue")
                                if k in meta})
                    row.update(scorer=name, is_signed=signed, raw_score=_scalar(data.X[i, j], self),
                               quantile_score=_scalar(quantiles[i, j], self) if quantiles is not None else None)
                    entry = (_rank(row), -count, row)
                    count += 1
                    if len(best) < MAX_ROWS:
                        heapq.heappush(best, entry)
                    elif entry[:2] > best[0][:2]:
                        heapq.heapreplace(best, entry)
        return {"rows": [row for _, _, row in sorted(best, reverse=True)], "n_rows": count,
                "scorers": [{"name": name, "is_signed": bool(self._metadata[name].is_signed)} for name in scorers]}

    def _check_active(self) -> None:
        if self.cancelled.is_set():
            raise reg.AlphaGenomeUnavailable("Atlas 조회 중단")


def _filter(values: object, pattern: str, label: str, limit: int = 20) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > limit or not all(
            isinstance(v, str) and re.fullmatch(pattern, v) for v in values):
        raise ValueError(f"{label} 형식 또는 {limit}개 상한을 확인하세요")
    return sorted(set(values))


def _summary(item: Item, top: int) -> dict:
    answer = item.answer
    rows = answer["rows"]
    text = "선택 조건에 Atlas 점수 없음. 변이 부재·필터 불일치와 효과 없음을 구분해야 합니다."
    if rows:
        row = rows[0]
        raw = row["raw_score"]
        direction = "방향 미제공"
        if row["is_signed"] and isinstance(raw, (int, float)):
            direction = "증가" if raw > 0 else "감소" if raw < 0 else "변화 없음"
        tissue = row.get("biosample_name") or row.get("ontology_curie") or "조직 정보 없음"
        gene = row.get("gene_name") or row.get("gene_id") or "유전자 지정 없음"
        text = f"Atlas 사전 계산 {row['scorer']} · {tissue} · {gene}: {direction}, raw={raw}."
    return {"variant": item.variant.key, "has_scores": bool(rows), "summary_ko": text,
            "n_rows": answer["n_rows"], "n_saved": len(rows), "truncated": answer["n_rows"] > len(rows),
            "scorers": answer["scorers"], "top": rows[:top]}


async def query_variants(annotator: reg.RegulatoryAnnotator, variants: object, *,
                         requested_scorers: list[str] | None = None, ontology_terms: list[str] | None = None,
                         gene_ids: list[str] | None = None, top: int = 20, assembly: str = "GRCh38") -> dict:
    if assembly != "GRCh38" or type(top) is not int or not 1 <= top <= 100:
        raise ValueError("Atlas는 GRCh38, top 1~100만 받습니다")
    scorers = _filter(["RNA_SEQ"] if requested_scorers is None else requested_scorers,
                      r"[A-Za-z0-9_]{1,100}", "requested_scorers", MAX_SCORERS)
    if not scorers:
        raise ValueError("requested_scorers는 하나 이상 필요합니다")
    terms = _filter(ontology_terms, r"[A-Z][A-Z0-9_]*:[0-9]+", "ontology_terms")
    genes = _filter(gene_ids, r"ENSG[0-9]{11}(?:\.[0-9]+)?", "gene_ids")
    values = [variants] if isinstance(variants, str) else variants
    if not isinstance(values, list) or not 1 <= len(values) <= reg.AG_MAX_PER_CALL:
        raise ValueError("Atlas 변이는 한 번에 1~10개입니다")
    backend = annotator.atlas_backend or AtlasBackend.from_env()
    if backend is None:
        raise LookupFailed(f"Atlas 키 없음: runner 환경변수 {KEY_ENV}가 필요합니다. 키 파일은 읽지 않습니다")
    if any(backend.redact(value) != value for value in scorers + terms + genes):
        raise ValueError("Atlas 필터에 인증 정보가 포함되어 조회하지 않았습니다")
    # Values enter records only after validation; never echo arbitrary input or pasted secrets.
    items = []
    for raw in values:
        try:
            if not isinstance(raw, str) or not re.fullmatch(r"(?:chr)?(?:[1-9]|1[0-9]|2[0-2]|X|Y):[1-9][0-9]{0,8}:[ACGTacgt][>:][ACGTacgt]", raw):
                raise ValueError
            query = reg._alphagenome_query(raw.replace(">", ":"), 1)
            if query.variant.ref == query.variant.alt:
                raise ValueError
            items.append(Item(query, backend.redact(query.key)))
        except ValueError:
            items.append(Item(None, "[invalid input]", error="Atlas는 GRCh38 SNV(chr:pos:ref>alt)만 받습니다"))
    version = backend.redact(await reg._alphagenome_call(backend.setup))
    # No dataset release is exposed: expire reuse daily and name this limitation, never invent a release.
    source = {**SOURCE, "sdk_version": version, "cache_epoch_utc": datetime.now(timezone.utc).date().isoformat()}
    release = f"AlphaGenome Atlas / SDK {version} / {source['cache_epoch_utc']}"
    options = {"assembly": assembly, "requested_scorers": scorers, "ontology_terms": terms,
               "gene_ids": genes, "schema": 2, "saved_rows_limit": MAX_ROWS}
    annotator._from_cache("alphagenome_atlas", release, options, items)
    done: dict[str, Any] = {}
    stopped = False
    for item in items:
        if item.error or item.cached_at:
            continue
        key = item.variant.key
        if key not in done:
            if stopped:
                done[key] = "앞 항목 연결·권한·SDK 오류 또는 시간 초과로 건너뜀"
            else:
                await annotator.http._pace("alphagenome")
                try:
                    done[key] = await reg._alphagenome_call(backend.query, item.variant, scorers, terms, genes)
                except PermissionError:
                    stopped = True
                    done[key] = "Atlas 인증·접근 권한 없음. 키의 Atlas 권한을 확인하세요"
                except (asyncio.TimeoutError, TimeoutError, reg.AlphaGenomeUnavailable):
                    backend.cancelled.set()
                    stopped = True
                    done[key] = "Atlas 연결 실패 또는 시간 초과"
                except asyncio.CancelledError:
                    backend.cancelled.set()
                    raise
                except (ValueError, IndexError):
                    done[key] = "Atlas 변이 미수록 또는 요청 조건 미지원. 효과 없음으로 해석하지 마세요"
                except NotImplementedError:
                    stopped = True
                    done[key] = "Atlas 서비스 또는 SDK가 이 조회를 지원하지 않습니다"
                except LookupFailed as exc:
                    # Only our fixed messages, never raw SDK exception strings.
                    stopped = True
                    done[key] = backend.redact(str(exc))
                except Exception:
                    stopped = True
                    done[key] = "Atlas 조회 실패(SDK·서비스 상태 확인)"
        if isinstance(done[key], str):
            item.error = done[key]
        else:
            item.answer = done[key]
    # Keep per-item failures in the same result file and counts ledger, including an all-error response.
    return annotator._finish("alphagenome_atlas", "Google DeepMind AlphaGenome Atlas", release, SOURCE["url"],
                             options, items, lambda item: _summary(item, top), source_details=source,
                             note=SOURCE["usage"] + " |quantile| 순 정렬이며 p값·병원성 확률이 아닙니다. "
                             "quantile 없는 행은 뒤에 둡니다. 자동 model inference 전환은 없습니다. "
                             "미수록 변이는 별도 승인한 alphagenome_variant 조회를 검토하세요.")
