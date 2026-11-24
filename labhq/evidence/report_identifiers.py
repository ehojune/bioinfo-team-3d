"""Report identifier existence checks. Registry matches do not establish claim support."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Mapping
from urllib.parse import quote, unquote, urlsplit

import httpx
from markdown_it import MarkdownIt

from ..edition_sources import _DOI, _DOI_HOSTS, _URL, _clean_doi
from .verify import LiveSourceResolver, LookupFailed, _json_object, _require_success

_GO = re.compile(r"(?<![A-Za-z0-9_])GO:[0-9]{7}(?![A-Za-z0-9_])", re.I)
_SYMBOL = r"[A-Z][A-Z0-9-]{0,39}"
_SYMBOL_ITEM = rf"[*`]*{_SYMBOL}(?![A-Za-z0-9_:-])[*`]*"
_GENE_SYMBOLS = re.compile(rf"(?i:\bHGNC(?:\s+symbols?)?\s*[:=]|\bhuman gene symbols?\s*[:=]|"
                           rf"사람 유전자 기호\s*[:=])\s*({_SYMBOL_ITEM}(?:[ \t]*,[ \t]*{_SYMBOL_ITEM})*)")
_DOI_VALUE = re.compile(r"10\.[0-9]{4,9}/[!-~]{1,512}")
_MARKDOWN = MarkdownIt("commonmark")
_CLAIM_ANCHOR = re.compile(r"\[\[\s*claim\s*:[^\]\n]+\]\]", re.I)
LIMIT = 40


def _candidates(text: str) -> Iterable[tuple[str, str]]:
    for url in _URL.findall(text):
        try:
            parts = urlsplit(url)
        except ValueError:
            continue
        if parts.netloc.lower() in _DOI_HOSTS:
            if _DOI_VALUE.fullmatch(doi := unquote(_clean_doi(parts.path[1:]))):
                yield "doi", doi.lower()
        elif parts.hostname and parts.hostname.lower() == "www.ebi.ac.uk":
            if ("/QuickGO/" in parts.path
                    and _GO.fullmatch(go := unquote(parts.path.rstrip("/").rsplit("/", 1)[-1]))):
                yield "go", go.upper()
    plain = _URL.sub(" ", _CLAIM_ANCHOR.sub(" ", text))
    yield from (("go", value.upper()) for value in _GO.findall(plain))
    for symbols in _GENE_SYMBOLS.findall(plain):
        yield from (("hgnc_symbol", item.strip(" \t*`")) for item in symbols.split(","))
    for match in _DOI.finditer(plain):
        if _DOI_VALUE.fullmatch(doi := _clean_doi(match.group())):
            yield "doi", doi.lower()


def _identifiers(texts: Iterable[str], ledgers: Mapping) -> Iterable[tuple[str, str]]:
    for text in texts:
        # Ignore fenced/indented examples and HTML blocks, but accept identifiers formatted as inline code.
        for token in _MARKDOWN.parse(text):
            if token.type == "inline":
                yield from _candidates(re.sub(r"<!--.*?-->", " ", token.content, flags=re.S))
    for ledger in ledgers.values():
        if not isinstance(ledger, Mapping):
            continue
        for field, content in (("claims", "statement"), ("evidence", "observation")):
            for row in ledger.get(field) or []:
                if not isinstance(row, Mapping):
                    continue
                yield from _candidates(str(row.get(content) or ""))
                source = row.get("source")
                if row.get("status") not in (None, "observed") or not isinstance(source, Mapping):
                    continue
                scheme, value = source.get("id_scheme"), str(source.get("id_value") or "")
                if scheme == "doi":
                    if _DOI_VALUE.fullmatch(value):
                        yield "doi", value.lower()
                    else:
                        yield from (candidate for candidate in _candidates(value) if candidate[0] == "doi")
                elif scheme in {"go", "go_id"} and _GO.fullmatch(value):
                    yield "go", value.upper()
                elif scheme == "hgnc_symbol" and re.fullmatch(_SYMBOL, value):
                    yield "hgnc_symbol", value
                yield from _candidates(str(source.get("uri") or ""))


def _records(payload: Mapping, count_key: str, rows_key: str) -> list[Mapping]:
    count, rows = payload.get(count_key), payload.get(rows_key)
    if (type(count) is not int or not isinstance(rows, list)
            or count < len(rows) or (not rows and count) or not all(isinstance(row, Mapping) for row in rows)):
        raise LookupFailed("invalid_response")
    return rows


class ReportIdentifierResolver(LiveSourceResolver):
    """Use the source verifier's HTTP transport and per-request admission for each registry send."""

    async def matches(self, client: httpx.AsyncClient, scheme: str, value: str) -> tuple[list[str], str]:
        if scheme == "hgnc_symbol":
            for field in ("symbol", "alias_symbol", "prev_symbol"):
                response = await self._request(client, "GET", f"https://rest.genenames.org/fetch/{field}/{value}")
                _require_success(response)
                payload = _json_object(response)
                header, result = payload.get("responseHeader"), payload.get("response")
                if not isinstance(header, Mapping) or header.get("status") != 0 or not isinstance(result, Mapping):
                    raise LookupFailed("invalid_response")
                records = _records(result, "numFound", "docs")
                if not records:
                    continue
                matches = []
                for record in records:
                    symbol, marked = record.get("symbol"), record.get(field)
                    if (not isinstance(symbol, str) or not re.fullmatch(_SYMBOL, symbol)
                            or not (value == marked if field == "symbol" else isinstance(marked, list) and value in marked)):
                        raise LookupFailed("invalid_response")
                    matches.append(symbol)
                state = "withdrawn" if all(row.get("status") == "Entry Withdrawn" for row in records) else field
                return sorted(set(matches)), state
            return [], ""
        if scheme == "go":
            response = await self._request(client, "GET", f"https://www.ebi.ac.uk/QuickGO/services/ontology/go/terms/{value}")
            _require_success(response)
            records = _records(_json_object(response), "numberOfHits", "results")
            if not records:
                return [], ""
            term = records[0].get("id")
            if len(records) != 1 or not isinstance(term, str) or not _GO.fullmatch(term):
                raise LookupFailed("invalid_response")
            return [term], ("obsolete" if records[0].get("isObsolete") is True else "secondary_id" if term != value else "")
        response = await self._request(client, "GET", f"https://doi.org/api/handles/{quote(value, safe='/')}")
        if response.status_code != 404:
            _require_success(response)
        payload = _json_object(response)
        handle = payload.get("handle")
        if not isinstance(handle, str) or handle.lower() != value:
            raise LookupFailed("invalid_response")
        code = payload.get("responseCode")
        if response.status_code == 404 and code == 100:
            return [], ""
        if response.status_code == 200 and code in (1, 200):
            return [value], ""
        raise LookupFailed("invalid_response")


async def check_identifiers(texts: Iterable[str], ledgers: Mapping, resolver: ReportIdentifierResolver,
                            *, deadline_s: float, known_dois: frozenset[str] = frozenset()) -> dict:
    """Deduplicate, bound the lookup batch, and keep failed sends separate from registry misses."""
    identifiers = list(dict.fromkeys(_identifiers(texts, ledgers)))
    slots = asyncio.Semaphore(4)

    async def check(client: httpx.AsyncClient, scheme: str, value: str) -> dict:
        if scheme == "doi" and value in known_dois:
            return {"status": "found", "matches": [value], "detail": ""}
        async with slots:
            try:
                matches, detail = await resolver.matches(client, scheme, value)
            except LookupFailed as error:
                return {"status": "unverified", "detail": error.kind}
        return {"status": "found" if matches else "not_found", "matches": matches, "detail": detail}

    async with httpx.AsyncClient(timeout=resolver.request_timeout_s, headers=resolver._headers,
                                 transport=resolver.transport, follow_redirects=False) as client:
        tasks = [asyncio.create_task(check(client, *item)) for item in identifiers[:LIMIT]]
        if tasks:
            try:
                await asyncio.wait(tasks, timeout=deadline_s)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    return {"items": [{"scheme": scheme, "value": value, **(
        task.result() if not task.cancelled() else {"status": "unverified", "detail": "deadline"}
    )} for (scheme, value), task in zip(identifiers, tasks)], "omitted": max(0, len(identifiers) - LIMIT)}
