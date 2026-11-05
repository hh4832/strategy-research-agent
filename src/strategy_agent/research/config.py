"""Agent-owned configuration envelope; no Core timing or cost calculations.

JSON only. SHA is canonical JSON with the top-level sha256 field set to null.
Changing any setting requires a new version and pin; history lives in each archive.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from strategy_agent.adapters import mrc_adapter as A
from strategy_agent.costs.profile import profile_hash, validate_profile
from strategy_agent.spec.validate import canonical_hash

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = ROOT / 'schemas' / 'RESEARCH_CONFIG_SCHEMA.json'


class ConfigError(ValueError):
    pass


def config_hash(config: dict) -> str:
    value = copy.deepcopy(config)
    value['sha256'] = None
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def load_config(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    errors = list(Draft202012Validator(json.loads(SCHEMA.read_text()),
                                     format_checker=FormatChecker()).iter_errors(value))
    if errors:
        raise ConfigError('; '.join(e.message for e in errors))
    if value['sha256'] != config_hash(value):
        raise ConfigError('configuration SHA mismatch')
    ws = value['windows']
    for w in ws.values():
        if datetime.fromisoformat(w['start']) >= datetime.fromisoformat(w['end']):
            raise ConfigError('window must be nonempty')
    if datetime.fromisoformat(ws['in_sample']['end']) > datetime.fromisoformat(ws['validation']['start']):
        raise ConfigError('in-sample and validation overlap')
    ids = [s['id'] for s in value['cost_scenarios']]
    if len(ids) != len(set(ids)) or 'BASE' not in ids:
        raise ConfigError('unique scenario IDs including BASE required')
    return value


def scenario_issues(scenario: dict, profiles: dict, approvers: set) -> list[str]:
    """Check metadata and lossless representability against the CURRENT pinned adapter.

    ALL_IN with separate exchange/regulatory components is ambiguous: refuse it.
    Core receives the existing multiplier; Agent never computes an adjusted fee.
    """
    p = profiles.get((scenario['profile_id'], scenario['version']))
    if p is None:
        return ['CostProfile unavailable']
    errors = [f'{i.rule}: {i.message}' for i in validate_profile(p, approvers)]
    if scenario['sha256'] != profile_hash(p) or p['provenance']['profile_sha256'] != scenario['sha256']:
        errors.append('CostProfile SHA mismatch')
    if scenario['currency'] != p['currency'] or scenario['approval_status'] != p['status']:
        errors.append('currency / approval mismatch; approval cannot be promoted')
    assumed = any(c['basis'] == 'RESEARCH_ASSUMPTION' for c in p['components'])
    if scenario['assumption_status'] != ('ASSUMED' if assumed else 'EVIDENCED'):
        errors.append('assumption status mismatch')
    if scenario['fee_basis'] == 'ALL_IN' and any(c['kind'] in ('EXCHANGE_FEE', 'REGULATORY_FEE') for c in p['components']):
        errors.append('all-in commission plus component fees would double count')
    kinds = {c['kind'] for c in p['components']}
    if scenario['commission_model'] != ('COMPONENTS' if 'COMMISSION' in kinds else 'NONE'):
        errors.append('commission model mismatch')
    slip = next((c for c in p['components'] if c['kind'] == 'SLIPPAGE'), {})
    if scenario['slippage_model'] != ('TICKS' if 'ticks' in slip else 'BPS' if 'bps' in slip else 'NONE'):
        errors.append('slippage model mismatch')
    instrument = 'FUTURES' if p['market'] == 'TXF' else 'EQUITY'
    gaps = A.unmappable_cost_components(p, instrument)
    if gaps or scenario['core_compatibility'] != 'PASS':
        errors.append('Core compatibility pending or unsupported: ' + '; '.join(gaps))
    if p['status'] != 'APPROVED':
        errors.append('CostProfile not APPROVED')
    return errors


def configured_jobs(config: dict, job, runner):
    """Bind a config to the existing Spec; never silently override spec fields.

    R02 is intentionally evaluated by ResearchRunner, which persists REJECTED_SPEC.
    Custom profiles can be configured, but execution needs a separately registered
    Spec with the same profile pin. No automatic Spec rewrite or approval occurs.
    """
    spec = job.spec
    expected = {'market': spec['universe']['market'], 'instrument': spec['universe']['symbols'][0],
                'timeframe': spec['universe']['timeframe'], 'spec_sha256': canonical_hash(spec),
                'holdout_id': spec['experiment']['holdout_lock_id'],
                'initial_capital': spec['position_sizing']['initial_capital'],
                'position_sizing': spec['position_sizing'], 'risk_limits': spec['risk_constraints']}
    for k, want in expected.items():
        if config[k] != want:
            raise ConfigError(f'{k} differs from registered Spec')
    if config['strategy']['id'] != spec['strategy_id']:
        raise ConfigError('strategy differs from registered Spec')
    ds = spec['data_requirements']['datasets'][0]
    if any(config['dataset'][k] != ds[k] for k in config['dataset']):
        raise ConfigError('dataset pin mismatch')
    tp = config['temporal_policy']
    if tp['version'] != '1' or tp['id'] != spec['execution']['convention'] or tp['sha256'] != hashlib.sha256(
            json.dumps(spec['execution'], sort_keys=True, separators=(',', ':')).encode()).hexdigest():
        raise ConfigError('temporal policy pin mismatch')
    if runner.env.kind != 'SYNTHETIC' or tp['status'] != 'SYNTHETIC_ONLY':
        raise ConfigError('formal temporal integration PENDING; no approval inferred')
    window = config['windows']['validation']
    if window != {'start': job.window_start, 'end': job.window_end} or config['windows']['in_sample'] != {
            'start': job.warmup_start, 'end': job.window_start}:
        raise ConfigError('only the registered synthetic windows are executable')
    out = []
    for scenario in config['cost_scenarios']:
        issues = scenario_issues(scenario, runner.env.cost_profiles, set(runner.env.governance.approvers))
        ref = spec['costs']['profile_ref']
        if any(scenario[k] != ref[k] for k in ('profile_id', 'version', 'sha256')):
            issues.append('scenario requires a separately registered Spec profile pin')
        if scenario['multiplier'] not in spec['costs']['sensitivity_multipliers']:
            issues.append('multiplier not pre-registered')
        if issues:
            raise ConfigError('; '.join(issues))
        out.append(replace(job, parameters=dict(config['strategy']['parameters']),
                           cost_multiplier=scenario['multiplier'], label=scenario['id'],
                           research_config=copy.deepcopy(config)))
    return out
