"""Static executable/safety contract; no Drive and no token required."""
import ast
import json
from pathlib import Path
import re


def test_first_cell_contains_all_manual_operations_and_safe_default():
    p=Path(__file__).parents[1]/'notebooks/Phase5C1_Long_Development_Readiness.ipynb'
    notebook=json.loads(p.read_text());cells=[c for c in notebook['cells'] if c['cell_type']=='code']
    code=[''.join(c['source']) for c in cells]
    for cell,source in zip(cells,code):
        assert cell['outputs']==[] and cell['execution_count'] is None
        tree=ast.parse(source)
        for n in ast.walk(tree):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Name):assert n.func.id!='input'
    first=code[0]
    for key in ('drive.mount','AGENT_SHA','CORE_SHA','RECOVERY_ARCHIVE','SNAPSHOT_ID','MODE','REVIEWED_DATA_PINS',
                'ORIGINAL_APPROVAL_EVIDENCE_PATH','NEW_AUTHORIZATION_ID','GOVERNANCE_ROOT','BIND_AUTHORIZATION_URL',
                'APPROVE_TASK_URL','CONFIRM_SETTINGS','DRIVE_OUTPUT_PARENT'):
        assert key in first
    assert "MODE = 'PREPARE_ONLY'" in first and 'REVIEWED_DATA_PINS = None' in first
    assert re.search(r'AGENT_SHA = "[0-9a-f]{40}"',first)
    assert "ZoneInfo('Asia/Taipei')" in first
    rest='\n'.join(code[1:]);calls=set()
    for source in code:
        for n in ast.walk(ast.parse(source)):
            if isinstance(n,ast.Call):calls.add(n.func.attr if isinstance(n.func,ast.Attribute) else getattr(n.func,'id',''))
    assert {'inspect_archive','stage_archive','prepare','execute_authorized'}<=calls
    assert not {'produce_development_snapshot','plan_conversion','signoff','consume','run_backtest'} & calls
    assert 'OBSERVED_NOT_TRUSTED' in notebook['cells'][0]['source'].__str__() or 'NOT automatically trusted' in rest
    assert "if MODE=='PREPARE_ONLY':" in rest and 'PREVIOUS_PREPARATION_DIRECTORY' in rest
    assert 'copytree(OUTPUT,DRIVE_OUTPUT)' in rest and 'ARCHIVE_INDEX.json' in rest
    for source in code[1:]:
        for n in ast.walk(ast.parse(source)):
            if isinstance(n,ast.Assign):
                assert not any(isinstance(t,ast.Name) and t.id in ('MODE','AGENT_SHA','CORE_SHA','REVIEWED_DATA_PINS') for t in n.targets)
