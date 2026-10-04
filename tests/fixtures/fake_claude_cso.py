"""A stand-in `claude` CLI for the CSO research PLAN (#222).

The claude_code adapter starts it exactly like the real CLI (`-p`, `--json-schema`, stream-json out), so
the plan goes through the same schema, adapter parsing and PLAN validation as a live run. Like a model, it
knows only what the prompt says: agent ids from the roster, pack keys from the pack catalog, and fixes named
in a correction. Without `pack_values_keys` it does what the live CSO did on 2026-10-01: it takes the
pack's reviewer questions for acceptance ids. It never writes `protocol.packs`, as the live CSO did not.

A request containing `[draft-mistakes]` first returns the live run's other mistakes (no primary outcomes,
an out-of-range count scale, invented acceptance ids) and repairs only what the correction names.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DOMAIN_FIELDS = {
    "donor_id": "obs.ind (8 donors demultiplexed by demuxlet)",
    "condition": "obs.stim (ctrl vs stim, IFN-beta 6 h)",
    "batch": "one 10x lane per condition; donors are crossed with condition",
    "count_scale": "raw_counts",
    "independent_replicates": 8,
    "replicate_definition": "one biological donor; cells are summed within donor x condition",
    "model": "pseudobulk",
    "model_family": "negative_binomial",
    "model_rationale": "Summed raw counts per donor x condition fit a negative binomial ~donor+condition model.",
    "batch_design": "identifiable",
    "conclusion_mode": "condition_effect",
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48]


def _prompt(argv: list[str]) -> str:
    prompt = argv[argv.index("-p") + 1]
    pointer = re.match(r"Read (\S+\.md) in the current directory", prompt)
    return Path(pointer.group(1)).read_text(encoding="utf-8") if pointer else prompt


def _section(prompt: str, start: str, end: str) -> str:
    begin = prompt.find(start)
    if begin < 0:
        return ""
    stop = prompt.find(end, begin)
    return prompt[begin:stop if stop >= 0 else len(prompt)]


def _catalog(prompt: str) -> list[dict]:
    rows = []
    for line in _section(prompt, "domain packs", "Contract rules:").splitlines():
        line = line.strip()
        if line.startswith("{"):
            rows.append(json.loads(line))
    return rows


def _pack_values(row: dict, *, invented_acceptance: bool) -> dict:
    keys = row.get("pack_values_keys")
    names = list((keys or {}).get("fields") or [field["name"] for field in row.get("fields") or []])
    validators = list((keys or {}).get("validators") or [v["id"] for v in row.get("validators") or []])
    if keys and not invented_acceptance:
        acceptance = list(keys.get("acceptance") or [])
    else:
        acceptance = [_slug(question) for question in row.get("reviewer_questions") or []]
    return {
        "fields": {name: DOMAIN_FIELDS.get(name, "not applicable to this design") for name in names},
        "validators": {vid: "Donor-level pseudobulk with the model fixed before CP1 satisfies it." for vid in validators},
        "acceptance": {aid: "Holds for this identifiable donor-level design; see the frozen protocol." for aid in acceptance},
    }


def _plan(prompt: str, *, mistakes: bool) -> dict:
    roster = re.findall(r"^- ([A-Za-z0-9_-]+): ", _section(prompt, "Team roster", "Runner compute"), re.M)
    intake = json.loads(_section(prompt, "copy it into `intake`):", "\n\n").split("\n", 1)[1].strip())
    worker = roster[0]
    statistics = {"applicable": True, "reason": "stimulated vs control within donor",
                  "estimand": "condition log2 fold change in CD14+ monocytes", "analysis_unit": "donor",
                  "comparison_groups": ["stim", "ctrl"], "multiple_testing": "Benjamini-Hochberg FDR",
                  "missing_and_exclusions": "donor x condition pseudobulk with < 10 cells is excluded",
                  "effect_size_and_interval": "log2 fold change with 95% interval",
                  "sensitivity_analyses": ["leave-one-donor-out"]}
    if not mistakes:
        statistics["primary_outcomes"] = ["per-gene log2 fold change and FDR"]
    packs = {row["key"]: _pack_values(row, invented_acceptance=mistakes) for row in _catalog(prompt)}
    if mistakes:
        for values in packs.values():
            if "count_scale" in values["fields"]:
                values["fields"]["count_scale"] = "normalized_counts"
    return {
        "schema_version": 2,
        "topics": ["single_cell_rna_seq"],
        "intake": intake,
        "brief": {"question": "Do CD14+ monocytes change expression after IFN-beta in GSE96583?",
                  "purpose": "Fix a donor-level pseudobulk DE contract on public data",
                  "subject": "GSE96583 public PBMC count matrix, CD14+ monocytes, 8 donors",
                  "scope": "public count matrix and cell-type labels only",
                  "deliverables": ["DE table", "QC summary"],
                  "completion_conditions": ["DE table with FDR and donor QC reported"],
                  "study_type": "comparative",
                  "primary_hypothesis": "IFN-beta stimulation changes CD14+ monocyte expression",
                  "null_or_alternatives": ["no donor-level change", "lane effect explains the change"],
                  "distinguishing_observations": ["ISG up-regulation consistent across donors"]},
        "protocol": {"revision": 1, "analysis_unit": "donor x condition pseudobulk",
                     "selection_criteria": ["CD14+ Mono cells with donor labels"],
                     "exclusion_criteria": ["doublets", "unassigned donors"],
                     "comparators": ["stim", "ctrl"], "primary_metrics": ["log2 fold change", "FDR"],
                     "validation_methods": ["leave-one-donor-out"], "resource_limits": ["local CLI only"],
                     "stop_conditions": ["fewer than 3 donors per condition"],
                     "approval_conditions": ["CP1 before execution"],
                     "data_boundaries": ["public GEO matrix only"], "not_applicable": {},
                     "statistics": statistics},
        "pack_values": packs,
        "checklist": {"pseudobulk": "step:s1", "qc": "step:s1", "batch": "step:s1"},
        "suggested_next": [],
        "clarifying_questions": [],
        "steps": [{"id": "s1", "agent_id": worker, "instruction": "Aggregate counts per donor and condition.",
                   "phase": "analysis", "claim_ids": ["c1"], "input_refs": ["GSE96583"],
                   "outputs": ["pseudobulk.tsv"], "checks": ["cells per donor"],
                   "evidence_slots": [{"id": "e1", "required": True, "description": "pseudobulk table"}],
                   "depends_on": []}],
        "recruit": [],
        "notes": "public data only",
    }


def _repair(plan: dict, problems: str) -> dict:
    """Fix only what the correction names, the way a model follows an error list."""
    statistics = plan["protocol"]["statistics"]
    if "primary_outcomes" in problems:
        statistics["primary_outcomes"] = ["per-gene log2 fold change and FDR"]
    for key, values in plan["pack_values"].items():
        prefix = re.escape(f"pack_values[{key}]")
        scale = re.search(prefix + r"\.fields\.count_scale must be one of \[([^\]]*)\]", problems)
        if scale:
            values["fields"]["count_scale"] = scale.group(1).split(",")[0].strip(" '\"")
        wanted = re.search(prefix + r"\.acceptance must contain \[([^\]]*)\]", problems)
        if wanted:
            ids = [part.strip(" '\"") for part in wanted.group(1).split(",") if part.strip()]
            values["acceptance"] = {rule_id: "Holds for this identifiable donor-level design." for rule_id in ids}
    return plan


def _check(value, schema: dict, root: dict, where: str = "plan") -> list[str]:
    """The subset of JSON Schema the PLAN schema uses; the real CLI enforces the same constraints."""
    if "$ref" in schema:
        schema = root["$defs"][schema["$ref"].rsplit("/", 1)[1]]
    if "anyOf" in schema:
        branches = [_check(value, option, root, where) for option in schema["anyOf"]]
        return [] if any(not errors for errors in branches) else [f"{where}: matches no anyOf branch"]
    if "const" in schema and value != schema["const"]:
        return [f"{where}: must be {schema['const']!r}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{where}: must be one of {schema['enum']}"]
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
    if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        return [f"{where}: must be integer"]
    if kind in types and not isinstance(value, types[kind]):
        return [f"{where}: must be {kind}"]
    errors = []
    if kind == "object":
        properties = schema.get("properties") or {}
        errors += [f"{where}.{name}: required" for name in schema.get("required") or [] if name not in value]
        if schema.get("additionalProperties") is False:
            errors += [f"{where}.{name}: not allowed" for name in value if name not in properties]
        extra = schema.get("additionalProperties")
        for name, item in value.items():
            if name in properties:
                errors += _check(item, properties[name], root, f"{where}.{name}")
            elif isinstance(extra, dict):
                errors += _check(item, extra, root, f"{where}.{name}")
    if kind == "array":
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{where}: needs {schema['minItems']} items")
        for index, item in enumerate(value):
            errors += _check(item, schema.get("items") or {}, root, f"{where}[{index}]")
    if kind == "string":
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{where}: too short")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{where}: does not match {schema['pattern']}")
    return errors


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print("2.9.9 (fake claude for labhq tests)")
        return 0
    sys.stdout.reconfigure(encoding="utf-8")
    with Path(".labhq", "fake_cli_prompts.jsonl").open("a", encoding="utf-8") as calls:  # what `-p` carried
        calls.write(json.dumps(argv[argv.index("-p") + 1][:200], ensure_ascii=False) + "\n")
    prompt = _prompt(argv)
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    request = _section(prompt, "PI's request:", "\0")
    plan = _plan(prompt, mistakes="[draft-mistakes]" in request)
    correction = request.find("failed validation")
    if correction >= 0:
        plan = _repair(_plan(prompt, mistakes=True) if "[draft-mistakes]" in request else plan,
                       request[correction:])
    errors = _check(plan, schema, schema)
    if errors:
        print("structured output does not match --json-schema: " + "; ".join(errors[:5]), file=sys.stderr)
        return 3
    session = "fake-cso-session"
    for event in ({"type": "system", "subtype": "init", "session_id": session, "model": "fake-sonnet",
                   "mcp_servers": []},
                  {"type": "result", "subtype": "success", "result": "research plan ready", "session_id": session,
                   "total_cost_usd": 0.0, "num_turns": 1, "structured_output": plan}):
        print(json.dumps(event, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
