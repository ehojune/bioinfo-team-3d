"""Bounded PubMed bibliography/abstract collection; no paper bodies or shell payloads.

API contract: https://www.ncbi.nlm.nih.gov/books/NBK25499/ (2026-11-17).
One ESearch snapshots up to 10,000 IDs; EFetch retrieves those exact IDs in batches.
Pages share an owned JSONL bundle, with byte ranges and page hashes. A run-specific manifest survives partial
failure/cancellation and is collected by the normal annotation-output boundary.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import PurePath
import stat
import uuid
import xml.etree.ElementTree as ET

import httpx

from ..adapters.owned import OwnedPathError, _held_parent, append_owned, write_owned_bytes
from .annot import Annotator, EUTILS, LookupFailed, QUERY_LOG, RESULT_DIR, ResponseTooLarge, _now, _sha256

DEFAULT_MAX_RECORDS = 1000
MAX_RECORDS = 10000  # PubMed ESearch cannot enumerate beyond this; never claim completeness there.
PAGE_SIZE = 100
PAGE_BYTES = 8 * 1024 * 1024
TOTAL_BYTES = 64 * 1024 * 1024


def _text(node: ET.Element | None) -> str:
    return "" if node is None else "".join(node.itertext()).strip()


def _records(body: bytes) -> list[dict]:
    root = ET.fromstring(body)
    if root.tag != "PubmedArticleSet" or root.find(".//ERROR") is not None:
        raise ValueError("invalid PubMed article set")
    rows = []
    for entry in root:
        if entry.tag not in {"PubmedArticle", "PubmedBookArticle"}:
            raise ValueError("unexpected PubMed record type")
        citation = entry.find("MedlineCitation") if entry.tag == "PubmedArticle" else entry.find("BookDocument")
        pmid = _text(citation.find("PMID")) if citation is not None else ""
        if not pmid.isascii() or not pmid.isdigit():
            raise ValueError("PubMed record has no numeric PMID")
        abstracts = [{"label": node.get("Label", ""), "text": _text(node)}
                     for node in entry.findall(".//Abstract/AbstractText")]
        rows.append({"pmid": pmid, "title": _text(entry.find(".//ArticleTitle")),
                     "abstract": abstracts,
                     "access_level": "abstract" if abstracts else "metadata",
                     # Preserve all returned bibliographic fields, not only the display projection.
                     "record_xml": ET.tostring(entry, encoding="unicode")})
    if len(rows) > PAGE_SIZE:
        raise ValueError("PubMed returned more records than requested")
    return rows


class PubmedCollection:
    def __init__(self, annotator: Annotator, query: str, max_records: int = DEFAULT_MAX_RECORDS):
        if not isinstance(query, str) or not query.strip() or len(query) > 4000 or any(c in query for c in "\r\n\x00"):
            raise ValueError("PubMed query must be a non-empty single line, at most 4000 characters")
        if type(max_records) is not int or not 1 <= max_records <= MAX_RECORDS:
            raise ValueError(f"max_records must be 1..{MAX_RECORDS}")
        if annotator.workdir is None:
            raise ValueError("LABHQ_WORKDIR is required to preserve PubMed results")
        self.annotator, self.root = annotator, annotator.workdir
        self.run_id = uuid.uuid4().hex
        self.path = f"{RESULT_DIR}/pubmed-{self.run_id}.json"
        self.bundle = f"{RESULT_DIR}/pubmed-{self.run_id}-pages.jsonl"
        self.bundle_identity = None
        self.bundle_hash = hashlib.sha256()
        self.seen: set[str] = set()
        self.bytes = 0
        self.state = {"tool": "pubmed_search", "query": query.strip(), "queried_at": _now(),
                      "source": {"db": "PubMed", "url": EUTILS,
                                 "request_sha256": _sha256({"query": query.strip(), "max_records": max_records})},
                      "max_records": max_records, "page_size": PAGE_SIZE,
                      "found_count": None, "fetched_count": 0, "unique_count": 0,
                      "complete": False, "stop_reason": "in_progress", "pages": []}

    def append_page(self, data: bytes) -> None:
        """Append exact bytes through a held parent; committed pages remain a readable prefix."""
        relative = PurePath(self.bundle)
        flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0)
        if self.bundle_identity is None:
            flags |= os.O_CREAT | os.O_EXCL
        with _held_parent(self.root, relative) as parent:
            with os.fdopen(parent.open_file(relative.name, flags), "ab") as target:
                info = os.fstat(target.fileno())
                identity = (info.st_dev, info.st_ino)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != self.bytes
                        or self.bundle_identity is not None and identity != self.bundle_identity):
                    raise OwnedPathError("PubMed page bundle was linked, replaced or resized")
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
                self.bundle_identity = identity
        self.bundle_hash.update(data)
        self.state["bundle"] = {"path": self.bundle, "bytes": self.bytes + len(data),
                                "sha256": self.bundle_hash.hexdigest(), "format": "jsonl-pages"}

    def checkpoint(self, reason: str) -> dict:
        self.state["stop_reason"] = reason
        text = json.dumps(self.state, ensure_ascii=False, indent=1) + "\n"
        write_owned_bytes(self.root, self.path, text.encode("utf-8"))
        return {"full_result": self.path, "query_log": QUERY_LOG,
                **{key: self.state[key] for key in ("found_count", "fetched_count", "unique_count",
                                                   "complete", "stop_reason")}}

    def finish(self, reason: str) -> dict:
        self.state["complete"] = reason == "exhausted"
        result = self.checkpoint(reason)
        digest = hashlib.sha256((json.dumps(self.state, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
        append_owned(self.root, QUERY_LOG, json.dumps({"tool": "pubmed_search", **self.state["source"],
                     "queried_at": self.state["queried_at"], **result,
                     "result_file": self.path, "result_sha256": digest.hexdigest()}, ensure_ascii=False) + "\n")
        return result

    async def run(self) -> dict:
        self.checkpoint("in_progress")
        try:
            response = await self.annotator.http.request("ncbi", "POST", f"{EUTILS}/esearch.fcgi",
                response_limit=1024 * 1024,
                data={"db": "pubmed", "term": self.state["query"], "retmode": "json",
                      "retmax": self.state["max_records"], "sort": "pub date"})
            response.raise_for_status()
            answer = response.json()["esearchresult"]
            count, ids = answer["count"], answer["idlist"]
            if ("ERROR" in answer or "errorlist" in answer or not str(count).isascii()
                    or not str(count).isdigit() or not isinstance(ids, list)
                    or any(not isinstance(i, str) or not i.isascii() or not i.isdigit() for i in ids)):
                raise ValueError("invalid PubMed search response")
            found = int(count)
            self.state.update(found_count=found, ids=ids, query_translation=answer.get("querytranslation"))
            expected = min(found, self.state["max_records"])
            if len(ids) != expected or len(set(ids)) != len(ids):
                return self.finish("invalid_search_ids")
            self.checkpoint("in_progress")
            for start in range(0, len(ids), PAGE_SIZE):
                batch = ids[start:start + PAGE_SIZE]
                response = await self.annotator.http.request("ncbi", "POST", f"{EUTILS}/efetch.fcgi",
                    response_limit=PAGE_BYTES, data={"db": "pubmed", "id": ",".join(batch), "retmode": "xml"})
                response.raise_for_status()
                rows = _records(response.content)
                text = json.dumps({"query": self.state["query"], "offset": start,
                                   "requested_pmids": batch, "records": rows}, ensure_ascii=False) + "\n"
                data = text.encode("utf-8")
                size = len(data)
                if size > PAGE_BYTES or self.bytes + size > TOTAL_BYTES:
                    return self.finish("byte_limit")
                digest = hashlib.sha256(data).hexdigest()
                self.append_page(data)
                pmids = [row["pmid"] for row in rows]
                byte_offset = self.bytes
                self.bytes += size
                self.state["pages"].append({"path": self.bundle, "sha256": digest, "bytes": size,
                                            "byte_offset": byte_offset,
                                            "offset": start, "record_count": len(rows)})
                self.state["fetched_count"] += len(rows)
                self.seen.update(pmids)
                self.state["unique_count"] = len(self.seen)
                self.checkpoint("in_progress")
                if len(pmids) != len(batch) or set(pmids) != set(batch):
                    return self.finish("page_id_mismatch")
            return self.finish("record_limit" if found > len(ids) else "exhausted")
        except asyncio.CancelledError:
            # wait_for must still see cancellation, so the shared deadline remains a timeout.
            self.checkpoint("interrupted")
            raise
        except ResponseTooLarge:
            return self.finish("response_byte_limit")
        except (LookupFailed, httpx.HTTPError):
            return self.finish("lookup_failed")
        except (ValueError, KeyError, TypeError, ET.ParseError):
            return self.finish("invalid_response")
