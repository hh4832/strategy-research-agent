"""Small LLM seam: offline fixtures or an explicitly supplied JSON completion. No network or SDK."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Protocol

ROOT=Path(__file__).resolve().parents[3]


class DraftAdapter(Protocol):
    adapter_id: str
    input_kind: str
    def draft(self, hypothesis: str) -> str: ...


def empty_draft(hypothesis):
    return {'title':'Unresolved research idea','hypothesis':{'question':hypothesis,'testable_claim':'UNKNOWN',
        'null_hypothesis':'UNKNOWN','expected_direction':'UNKNOWN','purpose':'Research proposal only; NOT_VERIFIED'},
        'market_mechanism':{'category':'UNKNOWN','causal_logic':'UNKNOWN','failure_modes':[]},
        'strategy_rules':{**{k:'UNKNOWN' for k in ('entry_condition','exit_condition','position_sizing','exit_policy',
                                                 'holding_horizon','signal_time','execution_time')},
                          'universe':{'market':'UNKNOWN','symbols':[],'timeframe':'UNKNOWN'}},
        'required_data':{'market':'UNKNOWN','symbols':[],'timeframe':'UNKNOWN','price_fields':[],
            'additional_features':[],'coverage':{'start':'UNKNOWN','end':'UNKNOWN'},'pit_requirements':[],
            'pit_timing':'UNKNOWN','source_ref':'UNKNOWN','availability':'UNKNOWN'},
        'research_variables':{'changes':[],'parameter_search':{'mode':'UNKNOWN','max_trials':None},
            'selection_basis':'UNKNOWN','prior_experiment_refs':[]},
        'challenger_view':{'failure_reason':'Which economic friction or competing mechanism could invalidate this idea? NOT_VERIFIED.',
            'falsification':'Which pre-registered observation would refute the claim? NOT_VERIFIED.',
            'market_beta':'Could matched market-beta exposure explain the apparent effect? NOT_VERIFIED.',
            'regime_dependence':'Could the proposed effect disappear in a different regime? NOT_VERIFIED.',
            'trade_concentration':'Could a few trades account for any apparent improvement? NOT_VERIFIED.'},
        'strategy_spec_fragment':None}


class FakeLLMAdapter:
    """Exact synthetic fixture completions; arbitrary ideas retain UNKNOWN, never invent rules/data."""
    adapter_id='FAKE_LLM_FIXTURE_V1'
    input_kind='SYNTHETIC_FIXTURE'

    def __init__(self, fixture_root=None):
        root=Path(fixture_root) if fixture_root else ROOT/'fixtures/proposal'
        self.examples=[json.loads(p.read_text()) for p in sorted(root.glob('*.json'))]

    def draft(self,hypothesis):
        for fixture in self.examples:
            if fixture.get('kind')!='SYNTHETIC_FIXTURE':raise ValueError('offline fixtures must be marked SYNTHETIC_FIXTURE')
            if fixture['user_hypothesis'].strip()==hypothesis.strip():
                return json.dumps(copy.deepcopy(fixture['llm_draft']),ensure_ascii=False)
        return json.dumps(empty_draft(hypothesis),ensure_ascii=False)


class FileDraftAdapter:
    """A pre-generated model completion; model identity is explicit but output remains NOT_VERIFIED."""
    input_kind='USER_HYPOTHESIS'

    def __init__(self,path,adapter_id='LOCAL_JSON_COMPLETION_NOT_VERIFIED'):
        self.path=Path(path);self.adapter_id=adapter_id

    def draft(self,hypothesis):
        return self.path.read_text(encoding='utf-8')
