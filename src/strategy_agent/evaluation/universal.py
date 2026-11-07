"""Phase 4-B: read-only, deterministic research evaluator. Never imports an executor or governance writer."""
from __future__ import annotations

import copy
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import statistics
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker

from . import metrics as M
from .research import trade_stats, MIN_RETURNS_FOR_TAIL, MIN_TRADES_FOR_RATIOS
from .sufficiency import assess

VERSION = '1.0.0'
ROOT = Path(__file__).resolve().parents[3]
SCHEMA = ROOT / 'schemas/RESEARCH_EVALUATOR_INPUT_SCHEMA.json'
REQUIRED_EVIDENCE = ('holdout', 'trades', 'fills', 'equity_curve', 'cost_breakdown',
                     'rejections', 'execution_diagnostics', 'data_quality', 'metrics', 'budget_consumption')
PROTOCOL = 'QUANT_EVALUATION_PROTOCOL v1.0'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def available(value, **extra):
    return {'status': 'AVAILABLE', 'value': value, **extra}


def absent(reason, status='NOT_AVAILABLE'):
    return {'status': status, 'reason': reason}


def val(doc, key, default=None):
    item = doc.get(key, {})
    return item.get('value', default) if item.get('status') == 'AVAILABLE' else default


def ts(value):
    out = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if out.utcoffset() is None:
        raise ValueError('timezone-aware timestamp required')
    return out.astimezone(timezone.utc)


def _equal(a, b):
    return (not isinstance(b, bool) and isinstance(b, (float, int)) and
            math.isfinite(b) and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9))


def _semantic(doc):
    errors = []
    source = doc['source']; start, end = (ts(source['sample_period'][k]) for k in ('start', 'end'))
    if start >= end: errors.append('SAMPLE_PERIOD: start must precede exclusive end')
    ZoneInfo(source['sample_period']['timezone'])
    for section, id_field in (('trades', 'trade_id'), ('fills', 'fill_id')):
        rows = val(doc, section, [])
        if len({r[id_field] for r in rows}) != len(rows): errors.append(section + ': duplicate IDs')
        for row in rows:
            times = [ts(row[k]) for k in (('entry_ts', 'exit_ts') if section == 'trades' else ('exec_ts',))]
            if any(t < start or t >= end for t in times): errors.append(section + ': timestamp outside [start,end)')
            if section == 'trades' and times[0] >= times[1]: errors.append('TRADE_CHRONOLOGY: entry >= exit')
            if section == 'fills' and 'signal_available_at' in row and ts(row['signal_available_at']) >= times[0]:
                errors.append('LOOK_AHEAD: fill must be strictly after signal_available_at')
    eq = val(doc, 'equity_curve', [])
    times = [ts(r['timestamp']) for r in eq]
    if any(a >= b for a, b in zip(times, times[1:])): errors.append('EQUITY_CHRONOLOGY: timestamps not strictly increasing')
    if any(t < start or t > end for t in times): errors.append('EQUITY_CHRONOLOGY: timestamp outside sample')
    for r in val(doc, 'rejections', []):
        if 'timestamp' in r and not start <= ts(r['timestamp']) < end: errors.append('REJECTION_CHRONOLOGY')
    cost = val(doc, 'cost_breakdown')
    if cost and not _equal(math.fsum(cost['components'].values()), cost['explicit_total']):
        errors.append('COST_RECONCILIATION: component sum differs from explicit_total')
    official = val(doc, 'metrics', {})
    if 'trades' in official and not isinstance(official['trades'],dict): errors.append('METRIC_SCHEMA: trades must be an object')
    diag = val(doc, 'execution_diagnostics', {})
    if diag.get('lookahead_violations', 0): errors.append('LOOK_AHEAD: upstream violations observed')
    stats = val(doc, 'statistical_evidence', {})
    if stats and any(stats[k] != source[s] for k, s in
                     (('source_run_id', 'run_id'), ('core_sha', 'core_sha'), ('manifest_sha256', 'manifest_sha256'))):
        errors.append('SOURCE_SHA: statistical evidence identity mismatch')
    if cost and isinstance(official.get('costs'),dict) and isinstance(official['costs'].get('total'),(int,float)) and not _equal(cost['explicit_total'],official['costs']['total']):
        errors.append('COST_RECONCILIATION: upstream total differs')
    for field,section in (('fills','fills'),('rejections','rejections')):
        if field in official and isinstance(official[field],int) and official[field]!=len(val(doc,section,[])):
            errors.append('COUNT_MISMATCH:'+field)
    folds = sorted(stats.get('folds', []), key=lambda f: ts(f['start']))
    if len({f['fold_id'] for f in folds}) != len(folds): errors.append('FOLD_ID: duplicate')
    for f in folds:
        if not start <= ts(f['start']) < ts(f['end']) <= end: errors.append('FOLD_WINDOW: out of sample')
    if any(ts(a['end']) > ts(b['start']) for a, b in zip(folds, folds[1:])): errors.append('FOLD_WINDOW: overlapping OOS')
    if folds:
        trades = [t for t in val(doc, 'trades', []) if t['status'] == 'VALID']
        for f in folds:
            assigned = [t for t in trades if t.get('fold_id') == f['fold_id']]
            if any(not ts(f['start']) <= ts(t['entry_ts']) < ts(t['exit_ts']) < ts(f['end']) for t in assigned):
                errors.append('FOLD_WINDOW: assigned trade outside its fold')
            if all('fold_id' in t for t in trades) and not _equal(sum(t['net_pnl'] for t in assigned), f['net_pnl']):
                errors.append('FOLD_PNL: supplied fold PnL differs from Core trades')
        if any(t.get('fold_id') not in {f['fold_id'] for f in folds} for t in trades):
            errors.append('FOLD_PNL: not every valid trade is assigned')
    for sc in val(doc, 'cost_scenarios', []):
        if any(sc[k] != source[k] for k in ('core_sha', 'dataset_snapshot', 'sample_period', 'strategy_id', 'parameter_family', 'timeframe', 'currency')):
            errors.append('SCENARIO_COMPARABILITY: source, strategy, dates or currency differs')
    scenarios = val(doc, 'cost_scenarios', [])
    if len({s['scenario_id'] for s in scenarios}) != len(scenarios): errors.append('SCENARIO_ID: duplicate')
    return errors


def _metric(value, name):
    return available(value, authority='AGGREGATE_OF_CORE_RECORDS', definition_ref='RESEARCH_EVALUATOR_INPUT_SCHEMA.json#/$defs/metric_definitions/' + name)


def _measure(doc):
    trades = val(doc, 'trades', []); valid = [t for t in trades if t['status'] == 'VALID']
    pnls = [t['net_pnl'] for t in valid]
    stats = trade_stats(pnls)
    out = {}
    for key, value in stats.items():
        out[key] = (absent(value['reason'], 'INSUFFICIENT_SAMPLE' if len(pnls) < MIN_TRADES_FOR_RATIOS else 'NOT_APPLICABLE')
                    if isinstance(value, dict) else _metric(value, 'trade_statistics'))
    eq = val(doc, 'equity_curve', []); equity = [r['equity'] for r in eq]
    initial = doc.get('initial_capital')
    for key in ('total_return', 'net_return', 'annualized_return', 'max_drawdown', 'drawdown_duration_seconds',
                'volatility', 'tail_loss', 'turnover', 'cost_to_gross_profit', 'exposure', 'rejection_rate',
                'worst_trade', 'holding_period_seconds'):
        out[key] = absent('required observations or context not supplied')
    if equity:
        values = equity
        out['max_drawdown'] = _metric(M.max_drawdown(values), 'max_drawdown')
        peak = equity[0]; began = ts(eq[0]['timestamp']); longest = 0.0
        for row in eq:
            time = ts(row['timestamp'])
            if row['equity'] >= peak:
                if began is not None: longest = max(longest, (time - began).total_seconds())
                peak = row['equity']; began = time
            else:
                if began is None: began = ts(doc['source']['sample_period']['start'])
                longest = max(longest, (time - began).total_seconds())
        out['drawdown_duration_seconds'] = _metric(longest, 'max_drawdown')
    if initial is not None and equity:
        out['total_return'] = _metric(equity[-1] / initial - 1, 'total_return')
        out['net_return'] = _metric(math.fsum(pnls) / initial, 'net_return')
        days = (ts(eq[-1]['timestamp']) - ts(eq[0]['timestamp'])).total_seconds() / 86400
        out['annualized_return'] = (absent('needs >=365.2425 elapsed days and explicit zero external cash flows', 'INSUFFICIENT_SAMPLE')
            if days < 365.2425 or val(doc, 'metrics', {}).get('external_cash_flows') != 0 or not _equal(equity[0],initial) else
            _metric((equity[-1] / initial) ** (365.2425 / days) - 1, 'annualized_return'))
    if len(equity) >= 3:
        rets = [b/a - 1 for a,b in zip(equity, equity[1:])]
        gaps = [(ts(b['timestamp']) - ts(a['timestamp'])).total_seconds() for a,b in zip(eq,eq[1:])]
        if doc.get('equity_sampling') == 'REGULAR' and len(set(gaps)) == 1:
            out['volatility'] = _metric(statistics.stdev(rets), 'volatility')
            out['volatility']['observation_seconds'] = gaps[0]
            if len(rets) >= MIN_RETURNS_FOR_TAIL:
                # Same empirical quantile definition as evaluation.research.tail_risk; no annualization.
                import numpy as np
                q = float(np.quantile(rets, 0.05))
                out['tail_loss'] = _metric({'quantile_05':q,'conditional_mean':statistics.mean(r for r in rets if r <= q)}, 'tail_loss')
            else: out['tail_loss'] = absent(f'{len(rets)} returns < existing minimum {MIN_RETURNS_FOR_TAIL}', 'INSUFFICIENT_SAMPLE')
        else:
            out['volatility'] = out['tail_loss'] = absent('irregular or unknown equity intervals; no assumed daily frequency')
    if pnls:
        out['worst_trade'] = _metric(min(pnls), 'trade_statistics')
        seconds = [(ts(t['exit_ts']) - ts(t['entry_ts'])).total_seconds() for t in valid]
        out['holding_period_seconds'] = _metric({'mean':statistics.mean(seconds),'min':min(seconds),'max':max(seconds)}, 'holding_period')
    fills = val(doc, 'fills', [])
    if equity and fills and all('notional' in f for f in fills):
        out['turnover'] = _metric(sum(f['notional'] for f in fills) / 2 / statistics.mean(equity), 'turnover')
    cost = val(doc, 'cost_breakdown', {})
    if valid and all('gross_pnl' in t for t in valid) and cost:
        profit = sum(max(t['gross_pnl'], 0) for t in valid)
        if profit > 0: out['cost_to_gross_profit'] = _metric(cost['explicit_total'] / profit, 'cost_to_gross_profit')
    diag = val(doc, 'execution_diagnostics', {})
    if 'exposure' in diag: out['exposure'] = {**_metric(diag['exposure'], 'exposure'), 'authority':'UPSTREAM_EXECUTION_DIAGNOSTIC'}
    rejected = val(doc, 'rejections', []); orders = diag.get('orders_submitted')
    if orders and all('order_id' in r for r in rejected) and len({r['order_id'] for r in rejected}) == len(rejected) and len(rejected) <= orders:
        out['rejection_rate'] = _metric(len(rejected) / orders, 'rejection_rate')
    # Existing upstream performance remains authoritative. A mismatch fails closed, not overwritten.
    official = val(doc, 'metrics', {}); mismatches = []
    pairs = {'total_return':official.get('total_return'), 'net_return':official.get('cost_adjusted_net_return'),
             'max_drawdown':official.get('max_drawdown')}
    pairs.update({k:official.get('trades', {}).get(k) for k in stats})
    for key, number in pairs.items():
        if number is None: continue
        if out[key]['status']=='NOT_AVAILABLE' and key in ('total_return','net_return','max_drawdown') and isinstance(number,(int,float)) and not isinstance(number,bool) and math.isfinite(number):
            out[key]={**_metric(number,key),'authority':'UPSTREAM_METRIC','verification':'NOT_TESTED_MISSING_RECONCILIATION_CONTEXT'}
        elif out[key]['status'] == 'AVAILABLE' and isinstance(number, (int,float)) and not isinstance(number,bool):
            if not _equal(out[key]['value'], number): mismatches.append('METRIC_MISMATCH:' + key)
            else: out[key].update(authority='UPSTREAM_METRIC', verification='MATCH')
    return out, assess(pnls, [t['net_pnl'] for t in trades if t['status'] == 'INVALIDATED']), mismatches


def _strata(doc):
    trades = [t for t in val(doc,'trades',[]) if t['status']=='VALID']; result=[]
    timezone_name=doc['source']['sample_period']['timezone']
    dimensions = {'calendar_year':lambda t:str(ts(t['exit_ts']).astimezone(ZoneInfo(timezone_name)).year),
        'symbol':lambda t:t['symbol'], 'holding_period':lambda t:str((ts(t['exit_ts'])-ts(t['entry_ts'])).total_seconds())+' seconds',
        'market_regime':lambda t:t.get('regime'), 'parameter_region':lambda t:t.get('parameter_region')}
    for dimension, key in dimensions.items():
        if not trades or any(key(t) is None for t in trades):
            result.append({'dimension':dimension,'group':'ALL','status':'NOT_AVAILABLE','reason':'complete labels not supplied'}); continue
        for label in sorted({key(t) for t in trades}):
            p=[t['net_pnl'] for t in trades if key(t)==label]
            result.append({'dimension':dimension,'group':label,'status':'AVAILABLE' if len(p)>=30 else 'INSUFFICIENT_SAMPLE',
                           'trade_count':len(p),'net_pnl':math.fsum(p)})
    for sc in val(doc,'cost_scenarios',[]):
        result.append({'dimension':'cost_scenario','group':sc['scenario_id'],'status':'AVAILABLE',
                       'net_pnl':sc['net_pnl'],'cost_multiplier':sc['cost_multiplier'],'evidence_ref':sc['evidence_ref']})
    if not val(doc,'cost_scenarios'):result.append({'dimension':'cost_scenario','group':'ALL','status':'NOT_AVAILABLE'})
    return result


def _gates(doc, metrics, sample):
    s=val(doc,'statistical_evidence',{}); g={f'G{i}':absent('required protocol evidence absent','NOT_TESTED') for i in range(1,12)}
    def gate(k, condition, evidence): g[k]={'status':'PASS' if condition else 'FAIL','evidence':evidence}
    if {'spec_status','validator_errors','spec_sha256','spec_hash_verified'} <= s.keys(): gate('G1',s['spec_status'] in ('REGISTERED','FROZEN') and s['validator_errors']==0 and s['spec_hash_verified'], s['evidence_ref'])
    if {'benchmark_core_sha','benchmark_status'} <= s.keys(): gate('G2',s['benchmark_core_sha']==doc['source']['core_sha'] and s['benchmark_status']=='BENCHMARK_PASS',s['evidence_ref'])
    if 'data_pins_verified' in s: gate('G3',s['data_pins_verified'] and val(doc,'data_quality',{}).get('status')=='PASS',s['evidence_ref'])
    folds=s.get('folds',[])
    if folds and doc.get('sample_role')=='OUT_OF_SAMPLE': gate('G4',sample['status']=='SUFFICIENT' and len(folds)>=6, {'sample':sample,'folds':len(folds)})
    if {'oos_sharpe','psr'} <= s.keys() and s.get('return_basis')=='OOS_DAILY_NET': gate('G5',s['oos_sharpe']>0 and s['psr']>=.95,s['evidence_ref'])
    if {'dsr','family_trials','family_trial_count_complete'} <= s.keys() and s.get('return_basis')=='OOS_DAILY_NET': gate('G6',s['dsr']>=.95 and s['family_trial_count_complete'],{'dsr':s['dsr'],'family_trials':s['family_trials']})
    if 'holm_adjusted_p' in s and s.get('return_basis')=='OOS_DAILY_NET': gate('G7',s['holm_adjusted_p']<=.05,s['evidence_ref'])
    stress=[c for c in val(doc,'cost_scenarios',[]) if c['cost_multiplier']==2]
    if stress and 'break_even_multiplier' in s: gate('G8',s['break_even_multiplier']>=2 and all(c['net_pnl']>0 for c in stress),stress)
    if folds and sum(f['net_pnl'] for f in folds)>0:
        total=sum(f['net_pnl'] for f in folds)
        gate('G9',sum(f['net_pnl']>0 for f in folds)/len(folds)>=.6 and max(f['net_pnl'] for f in folds)/total<=.4,folds)
    if s.get('search_mode')=='FIXED': gate('G10',True,'declared FIXED spec; neighboring grid condition NOT_APPLICABLE')
    elif {'neighbor_sharpe_ratio','parameter_settings'} <= s.keys() and (s['parameter_settings']<10 or 'pbo' in s):
        gate('G10',s['neighbor_sharpe_ratio']>=.5 and (s['parameter_settings']<10 or s['pbo']<.2),s['evidence_ref'])
    if 'max_drawdown_halt' in s and all(metrics[k]['status']=='AVAILABLE' for k in ('max_drawdown','exposure','turnover')):
        gate('G11',abs(metrics['max_drawdown']['value'])<=s['max_drawdown_halt'],s['evidence_ref'])
    return g


def _robustness(doc, strata):
    s=val(doc,'statistical_evidence',{}); rows=[]
    for name in ('in_sample_vs_out_of_sample','cost_sensitivity','parameter_stability','temporal_stability',
                 'concentration','multiple_testing','selection_bias','survivorship_bias','look_ahead'):
        rows.append({'dimension':'robustness','group':name,'status':'NOT_TESTED','reason':'required evidence absent'})
    by={r['group']:r for r in rows}
    folds=s.get('folds',[])
    if folds:
        by['temporal_stability'].update(status='AVAILABLE',evidence=folds)
        if all('is_score' in f and 'oos_score' in f for f in folds):
            comparison=M.oos_stability([f['is_score'] for f in folds],[f['oos_score'] for f in folds])
            by['in_sample_vs_out_of_sample'].update(status='AVAILABLE',evidence={k:v if math.isfinite(v) else absent('undefined IS/OOS degradation') for k,v in comparison.items()})
    if val(doc,'cost_scenarios'): by['cost_sensitivity'].update(status='AVAILABLE',evidence=val(doc,'cost_scenarios'))
    if any(r['dimension']=='parameter_region' and r['status']=='AVAILABLE' for r in strata):
        by['parameter_stability'].update(status='AVAILABLE',evidence=[r for r in strata if r['dimension']=='parameter_region'])
    if val(doc,'trades'): by['concentration'].update(status='AVAILABLE',reason='trade/year/symbol contribution flags; no new cutoff')
    if 'family_trials' in s: by['multiple_testing'].update(status='AVAILABLE',evidence={k:s[k] for k in ('family_trials','dsr','holm_adjusted_p') if k in s})
    for name in ('selection_bias','survivorship_bias'):
        if s.get(name+'_checked') is True: by[name].update(status='AVAILABLE',evidence_ref=s['evidence_ref'],reason='upstream check reported; evaluator does not reproduce universe construction')
    diag=val(doc,'execution_diagnostics',{})
    fills=val(doc,'fills',[])
    if diag.get('lookahead_checked') is True and diag.get('lookahead_violations')==0:
        by['look_ahead'].update(status='AVAILABLE',reason='upstream check reported; fill times also verified where supplied')
    elif fills and all('signal_available_at' in f for f in fills):
        by['look_ahead'].update(status='NOT_TESTED',evidence={'observed_fill_timing':'PASS'},reason='observed fills strictly after signal availability; signal calculation itself not verified')
    return rows


def _flags(doc, metrics, sample, strata, robustness):
    flags=[]
    def add(code,evidence,severity='REVIEW'): flags.append({'code':code,'severity':severity,'evidence':evidence})
    p=[t['net_pnl'] for t in val(doc,'trades',[]) if t['status']=='VALID']; net=math.fsum(p)
    if sample['status']!='SUFFICIENT':add('INSUFFICIENT_SAMPLE',sample)
    if p and net<=0:add('NON_POSITIVE_CLOSED_NET_PNL',{'net_pnl':net})
    if p and net>0 and max(p)>=net:add('SINGLE_TRADE_COVERS_ALL_NET_PROFIT',{'largest_trade':max(p),'total_net':net})
    for dimension in ('symbol','calendar_year'):
        rows=[r for r in strata if r['dimension']==dimension and 'net_pnl' in r]
        if rows and net>0:
            top=max(rows,key=lambda r:r['net_pnl'])
            if top['net_pnl']>=net:add('SINGLE_'+dimension.upper()+'_COVERS_ALL_NET_PROFIT',top)
    regions=[r for r in strata if r['dimension']=='parameter_region' and 'net_pnl' in r]
    if len(regions)>1 and sum(r['net_pnl']>0 for r in regions)==1:
        add('ONLY_ONE_OBSERVED_PARAMETER_REGION_POSITIVE',regions)
    gross=[t['gross_pnl'] for t in val(doc,'trades',[]) if t['status']=='VALID' and 'gross_pnl' in t]
    if len(gross)==len(p) and gross and sum(gross)>0 and net<=0:
        add('POSITIVE_GROSS_NEGATIVE_NET',{'gross_closed_pnl':sum(gross),'net_closed_pnl':net})
    for sc in val(doc,'cost_scenarios',[]):
        if net>0 and sc['net_pnl']<=0:add('COST_SCENARIO_TURNS_NON_POSITIVE',sc)
    if metrics['tail_loss']['status']!='AVAILABLE':add('TAIL_RISK_NOT_ESTABLISHED',metrics['tail_loss'])
    if metrics['rejection_rate']['status']=='AVAILABLE' and metrics['rejection_rate']['value']>0:
        add('REJECTIONS_OBSERVED',metrics['rejection_rate'])  # no unapproved "large" rejection threshold
    days=(ts(doc['source']['sample_period']['end'])-ts(doc['source']['sample_period']['start'])).total_seconds()/86400
    if days<365.2425:add('LESS_THAN_ONE_YEAR_OBSERVED',{'elapsed_days':days})
    if val(doc,'holdout',{}).get('status')!='NOT_ACCESSED':add('HOLDOUT_STATUS_UNRESOLVED',val(doc,'holdout',{}))
    for r in robustness:
        if r['status']=='NOT_TESTED':add(r['group'].upper()+'_NOT_TESTED',r)
    if doc['kind']=='SYNTHETIC_FIXTURE':add('SYNTHETIC_FIXTURE',{'meaning':'engineering test only; no real strategy conclusion'})
    return sorted(flags,key=lambda f:f['code'])


def evaluate(doc, *, evaluator_commit_sha, evaluated_at=None):
    """Pure evaluation of provided finished-result records; missing evidence returns BLOCKED/RETEST."""
    import re
    if not re.fullmatch('[0-9a-f]{40}',evaluator_commit_sha):raise ValueError('full evaluator commit SHA required')
    at=evaluated_at or datetime.now(ZoneInfo('Asia/Taipei')).isoformat()
    ts(at)
    source=doc.get('source',{}) if isinstance(doc,dict) else {}
    if not isinstance(source,dict):source={}
    metadata={'source_run_id':source.get('run_id') if isinstance(source.get('run_id'),str) else None,'source_archive_path':source.get('archive_path') if isinstance(source.get('archive_path'),str) else None,
              'source_commit_sha':source.get('commit_sha') if isinstance(source.get('commit_sha'),str) and re.fullmatch('[0-9a-f]{40}',source['commit_sha']) else None,'evaluator_commit_sha':evaluator_commit_sha,
              'evaluation_timestamp':at,'schema_version':'1.0.0','evaluator_version':VERSION}
    checker=FormatChecker(formats=[])
    @checker.checks('date-time')
    def aware_datetime(value):
        if not isinstance(value,str):return True  # JSON Schema type check reports this separately.
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})',value):return False
        try:ts(value);return True
        except (ValueError,TypeError):return False
    errors=[f'{"/".join(map(str,e.absolute_path))}: {e.message}' for e in Draft202012Validator(json.loads(SCHEMA.read_text()),format_checker=checker).iter_errors(doc)]
    try: input_sha=digest(doc)
    except (TypeError,ValueError):input_sha=None;errors.append('NON_FINITE_OR_NON_JSON_INPUT')
    fatal=[]
    if not errors:
        try: fatal=_semantic(doc)
        except (ValueError,KeyError,TypeError):fatal=['TIMESTAMP_OR_TIMEZONE: invalid']
        for name in REQUIRED_EVIDENCE:
            if doc[name]['status']!='AVAILABLE':errors.append(f'REQUIRED_EVIDENCE:{name}:{doc[name]["status"]}')
        if val(doc,'holdout',{}).get('status')!='NOT_ACCESSED':errors.append('HOLDOUT: only unopened Development result archives accepted in v1')
        if not val(doc,'equity_curve',[]):errors.append('EQUITY_CURVE: empty')
        if val(doc,'data_quality',{}).get('status')!='PASS':
            (fatal if val(doc,'data_quality',{}).get('status')=='FAIL' else errors).append('DATA_QUALITY: not PASS')
        if val(doc,'execution_diagnostics',{}).get('status')!='PASS':
            (fatal if val(doc,'execution_diagnostics',{}).get('status')=='FAIL' else errors).append('EXECUTION_QUALITY: not PASS')
    engineering='FAIL' if fatal else 'BLOCKED' if errors else 'PASS'
    result={'metadata':metadata,'input_sha256':input_sha,'engineering_status':engineering,
        'strategy_verdict':'RETEST','evidence_sufficiency':'UNDETERMINED','errors':sorted(errors+fatal),
        'layers':{},'metrics':{},'risk_flags':[],'robustness_matrix':[],
        'protocol_gates':{},'research_decision':{'verdict':'RETEST','reasons':['missing or untrusted required evidence; no positive effect implied']},
        'evaluation_batch_consumption':0,'registry_written':False}
    if errors or fatal:
        result['risk_flags']=[{'code':'INVALID_OR_MISSING_EVIDENCE','severity':'BLOCKER','evidence':e} for e in result['errors']]
        result['robustness_matrix']=[{'dimension':'robustness','group':k,'status':'NOT_TESTED','reason':'input blocked'} for k in ('cost_sensitivity','temporal_stability','parameter_stability','multiple_testing','selection_bias','survivorship_bias','look_ahead')]
        return result
    metrics,sample,mismatches=_measure(doc)
    if mismatches:
        result.update(engineering_status='FAIL',errors=mismatches,risk_flags=[{'code':'METRIC_MISMATCH','severity':'BLOCKER','evidence':mismatches}])
        result['upstream_metrics']=copy.deepcopy(val(doc,'metrics'))
        return result
    strata=_strata(doc);robustness=_robustness(doc,strata);gates=_gates(doc,metrics,sample)
    flags=_flags(doc,metrics,sample,strata,robustness)
    result.update(metrics=metrics,upstream_metrics=copy.deepcopy(val(doc,'metrics')),protocol_gates=gates,
                  risk_flags=flags,robustness_matrix=strata+robustness)
    result['layers']={'data_quality':doc['data_quality'],'execution_quality':doc['execution_diagnostics'],
        'statistical_evidence':{'sample':sample,'protocol_gates':gates},'cost_sensitivity':doc.get('cost_scenarios',absent('not supplied','NOT_TESTED')),
        'robustness':robustness,'research_decision':None}
    enough=sample['status']=='SUFFICIENT'
    if enough:
        failed=[k for k,v in gates.items() if v['status']=='FAIL']
        if metrics['net_pnl']['value']<=0 or failed:
            verdict='REJECT';reasons=['non-positive closed net PnL or observed protocol gate failure']+failed
        elif (all(v['status']=='PASS' for v in gates.values()) and doc['kind']!='SYNTHETIC_FIXTURE'
              and all(val(doc,'statistical_evidence',{}).get(k) is True for k in ('mintrl_satisfied','selection_bias_checked','survivorship_bias_checked'))
              and val(doc,'execution_diagnostics',{}).get('lookahead_checked') is True
              and val(doc,'execution_diagnostics',{}).get('lookahead_violations')==0):
            verdict='KEEP';reasons=['existing protocol G1–G11 passed on provided source-bound evidence; research retention only']
        else:verdict='RETEST';reasons=['positive PnL alone is insufficient; complete protocol evidence or independent validation absent']
        result.update(strategy_verdict=verdict,evidence_sufficiency='DESCRIPTIVE_ONLY' if verdict!='KEEP' else 'PROTOCOL_GATES_SATISFIED')
        result['research_decision']={'verdict':verdict,'reasons':reasons}
    else:
        result['research_decision']={'verdict':'RETEST','reasons':['INSUFFICIENT_SAMPLE; evidence sufficiency UNDETERMINED']}
    result['layers']['research_decision']=result['research_decision']
    return result


def write_evaluation(result, output_directory):
    """Write five new artifacts exclusively outside source archive and formal registry/governance."""
    out=Path(output_directory).resolve()
    source=result['metadata'].get('source_archive_path')
    forbidden=[ROOT/'registry',ROOT/'governance']+([Path(source).resolve()] if source else [])
    if any(out==p or p in out.parents for p in forbidden):raise ValueError('evaluation output must be outside original archive and governed directories')
    out.mkdir(parents=True,exist_ok=False)
    meta=result['metadata']
    payloads={'evaluation.json':result,'risk_flags.json':{'metadata':meta,'risk_flags':result['risk_flags']},
              'research_decision.json':{'metadata':meta,**result['research_decision'],'engineering_status':result['engineering_status'],
                                        'evidence_sufficiency':result['evidence_sufficiency']}}
    for name,value in payloads.items():
        with (out/name).open('x') as f:f.write(json.dumps(value,indent=2,sort_keys=True,ensure_ascii=False,allow_nan=False)+'\n')
    lines=['# Research evaluation','',*[f'- {k}: {v if v is not None else "MISSING"}' for k,v in meta.items()],
           '',f'Engineering: **{result["engineering_status"]}**',f'Strategy Verdict: **{result["strategy_verdict"]}**',
           f'Evidence Sufficiency: **{result["evidence_sufficiency"]}**','',
           'RETEST is not evidence of a positive effect. KEEP is research retention, never permission to trade.',
           'SYNTHETIC_FIXTURE outputs are engineering evidence only.','', '## Independent layers','']
    for key,value in result['layers'].items():lines.extend([f'### {key}', '', '```json',json.dumps(value,sort_keys=True,indent=2,ensure_ascii=False),'```',''])
    lines+=['## Metrics','', '```json',json.dumps(result['metrics'],indent=2,sort_keys=True),'```','',
            '## Errors and opponent flags','', '```json',json.dumps({'errors':result['errors'],'flags':result['risk_flags']},indent=2,sort_keys=True),'```','',
            'Evaluator batch consumption: 0. No registry updates.']
    with (out/'evaluation_report.md').open('x') as f:f.write('\n'.join(lines)+'\n')
    with (out/'robustness_matrix.csv').open('x',newline='') as f:
        fields=list(meta)+['dimension','group','status','trade_count','net_pnl','cost_multiplier','reason','evidence_json']
        w=csv.DictWriter(f,fieldnames=fields,lineterminator='\n');w.writeheader()
        for row in result['robustness_matrix'] or [{'dimension':'all','group':'all','status':'NOT_TESTED','reason':'required evidence unavailable'}]:
            simple={k:row.get(k,'') for k in fields if k not in meta}
            simple['evidence_json']=canonical({k:v for k,v in row.items() if k not in fields})
            w.writerow({**meta,**simple})
    return out
