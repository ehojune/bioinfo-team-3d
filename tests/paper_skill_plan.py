"""Pure synthetic ResearchPlan fixture shared by paper preparation/recovery contracts."""
import copy


def plan_for_paper_rows(rows):
    step_ids = list(dict.fromkeys(sid for row in rows for sid in row['step_ids']))
    return {
        'schema_version': 2,
        'intake': {'work_kind': 'research', 'reason': 'Synthetic lifecycle contract',
                   'scope_status': 'in_scope', 'source': 'explicit'},
        'brief': {'question': 'Is the synthetic method reproducible?', 'purpose': 'Fixture validation',
                  'subject': 'Synthetic records', 'scope': 'Fake inputs only', 'study_type': 'technical',
                  'deliverables': ['Fixture receipt'], 'completion_conditions': ['Receipt verified']},
        'protocol': {'revision': 1, 'analysis_unit': 'synthetic record',
                     'selection_criteria': ['Synthetic fixture'], 'exclusion_criteria': [], 'comparators': [],
                     'primary_metrics': ['Receipt agreement'], 'validation_methods': ['Exact fixture check'],
                     'resource_limits': ['Existing request/task caps'], 'stop_conditions': ['Approval drift'],
                     'approval_conditions': ['CP1 before preparation'], 'data_boundaries': ['Synthetic only'],
                     'statistics': {'applicable': False, 'reason': 'Lifecycle contract, no statistical inference'}},
        'pack_values': {}, 'clarifying_questions': [],
        'steps': [{'id': sid, 'agent_id': 'worker', 'instruction': 'Check synthetic receipt',
                   'phase': 'validation', 'claim_ids': [f'claim-{sid}'], 'input_refs': ['synthetic-input'],
                   'outputs': [f'{sid}.json'], 'checks': ['Exact receipt agreement'],
                   'evidence_slots': [{'id': f'evidence-{sid}', 'required': True,
                                       'description': 'Synthetic receipt'}], 'depends_on': []}
                  for sid in step_ids],
        'paper_skill_results': copy.deepcopy(rows), 'recruit': [], 'notes': 'Synthetic approved lifecycle',
    }
