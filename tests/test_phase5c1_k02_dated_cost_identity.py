"""K02 against the pinned Core resolver; synthetic prices and rates only."""
import copy
import json
from pathlib import Path
from dataclasses import asdict, replace
from hashlib import sha256

import pytest

pd = pytest.importorskip("pandas")

core = pytest.importorskip("market_research_core")
pytest.importorskip("market_research_core.dated_costs")
from market_research_core.dated_costs import DatedCostSchedule, SCENARIOS, rate_record_sha256, schedule_sha256
from strategy_agent.adapters import mrc_adapter as A
from strategy_agent.adapters.m3a4_contract import validate_conservative_result
from strategy_agent.ports import ContractViolation
from strategy_agent.research.environment import synthetic_componentized_cost_profile


@pytest.fixture
def schedule():
    base = A.core_cost_profile(synthetic_componentized_cost_profile(), instrument_type='EQUITY', tick_size=.01, core=core)
    added = ('commission_cap_rate', 'half_spread_bps', 'equity_commission_per_share',
             'exchange_fee_per_unit', 'clearing_fee_per_unit')
    base = replace(base, approval_status='DRAFT', equity_commission_rate=0, equity_commission_per_share=.005, minimum_commission=1,
                   commission_scheme='FIXED', commission_cap_rate=.01, half_spread_bps=0, sell_tax_rate=0, slippage_ticks=0,
                   exchange_fee_per_unit=0, clearing_fee_per_unit=0, fee_rounding='HALF_UP', tax_rounding='HALF_UP',
                   component_basis={**base.component_basis, **{k:'ASSUMED' for k in added}},
                   component_evidence={**base.component_evidence, **{k:'SYNTHETIC' for k in added}},
                   component_approval_status={**base.component_approval_status, **{k:'PENDING' for k in added}})
    def rate(kind, start, end, amount):
        r = dict(version='SYNTHETIC-'+kind+start, effective_from=start, effective_to=end, rate=amount,
                 unit='USD_PER_NOTIONAL' if kind == 'SEC' else 'USD_PER_SHARE',
                 source_ref='SYNTHETIC_FIXTURE: known answer', source_basis='ASSUMED',
                 verification_status='SYNTHETIC_FIXTURE', source_document_sha256=sha256(b'synthetic').hexdigest(),
                 record_sha256=None)
        r['record_sha256'] = rate_record_sha256(r)
        return r
    doc = dict(contract='tsm-dated-trading-costs-v1', schedule_id='SYNTHETIC-K02', version='1.0.0',
               schedule_sha256=None, scope='RESEARCH_ONLY', evidence_kind='SYNTHETIC_FIXTURE',
               symbol='TSM', market='IBKR_EQUITY', timezone='America/New_York',
               coverage={'start':'2025-01-07', 'end':'2025-01-08'}, scenarios=copy.deepcopy(SCENARIOS),
               base_profile=asdict(base), base_profile_sha256=base.sha256,
               sec_rates=[rate('SEC','2025-01-07','2025-01-07','0.00001'),
                          rate('SEC','2025-01-08','2025-01-08','0.00002')],
               taf_rates=[rate('TAF','2025-01-07','2025-01-08','0.0002')])
    doc['schedule_sha256'] = schedule_sha256(doc)
    return DatedCostSchedule.from_document(doc, expected_sha256=doc['schedule_sha256'])


def frame():
    raw = pd.DataFrame([dict(source_timestamp=t, symbol='TSM', contract='TSM', open=100., high=101.,
                            low=99., close=100., volume=100000.)
                        for day in ('2025-01-07','2025-01-08')
                        for t in pd.date_range(day+' 10:00', periods=20, freq='min', tz='America/New_York')])
    return core.prepare_conservative_bars(raw, bar_minutes=1)


def run(schedule, dated=True):
    # Two legal same-session executions on opposite sides of the rate change.
    bars = frame()
    def order(oid, signal, placed, side):
        return core.ExecutionOrder(symbol='TSM', market='IBKR_EQUITY', contract='TSM', signal_time=signal,
                                   signal_available_at=signal, order_time=placed, side=side, quantity=100,
                                   strategy_id='SYNTHETIC', experiment_id='SYNTHETIC', time_in_force='DAY', order_id=oid)
    t = lambda text: pd.Timestamp(text, tz='America/New_York')
    orders = [order('before',t('2025-01-07 10:02'),t('2025-01-07 10:02'),'BUY'),
              order('after',t('2025-01-08 10:02'),t('2025-01-08 10:02'),'SELL')]
    cfg = core.ExecutionConfig(initial_cash=100000, bar_label='UNKNOWN', session_semantics='CONFIRMED',
                               timestamp_timezone='America/New_York', available_at_policy=A.CONSERVATIVE,
                               temporal_policy=core.CONSERVATIVE_POLICY, permitted_usage_scope='RESEARCH_ONLY',
                               dataset_snapshot_id='SYNTHETIC_K02')
    kwargs = {'cost_schedule':schedule} if dated else {}
    result = core.ExecutionEngine().run(bars, orders, cost_profile=schedule.base_profile, config=cfg, **kwargs)
    assert len(result.fills) == 2, result.rejections.to_dict('records')
    return result, orders


def validate(result, orders, schedule=None, profile=None):
    return validate_conservative_result(result, orders=orders, cost_profile=profile or schedule.base_profile,
        policy=core.CONSERVATIVE_POLICY, core_sha=result.provenance['engine_git_sha'], cost_schedule=schedule)


def mutate(result, column, value):
    # Core ledger is a fresh copy of fills, preserving the K00 contract.
    result.fills[column] = value


def test_static_base_profile_unchanged(schedule):
    r, orders = run(schedule, dated=False)
    assert validate(r, orders, profile=schedule.base_profile)['K02_ledger_cost_identities'] == 'PASS'
    mutate(r, 'cost_profile_sha256', 'wrong')
    with pytest.raises(ContractViolation, match='K02_ledger_cost_identities'):
        validate(r, orders, profile=schedule.base_profile)


@pytest.mark.parametrize('scenario', SCENARIOS)
def test_valid_dated_profile_and_execution_not_signal_date(schedule, scenario):
    schedule = replace(schedule, scenario=scenario)
    r, orders = run(schedule)
    assert validate(r, orders, schedule)['K02_ledger_cost_identities'] == 'PASS'
    before, after = r.ledger.to_dict('records')
    assert before['cost_profile_sha256'] != after['cost_profile_sha256'] != schedule.base_profile.sha256
    assert after['signal_time'] < after['exec_ts']
    assert after['cost_profile_sha256'] == schedule.profile_for(after['exec_ts']).sha256


@pytest.mark.parametrize('column,value', [('cost_profile_sha256','wrong'), ('cost_profile_version','wrong'),
                                         ('cost_profile_id','wrong'), ('commission_scope','wrong')])
def test_incorrect_resolved_identity_rejected(schedule, column, value):
    r, orders = run(schedule)
    mutate(r, column, value)
    with pytest.raises(ContractViolation, match='K02_ledger_cost_identities'):
        validate(r, orders, schedule)


def test_incorrect_submitted_base_rejected_even_if_provenance_agrees(schedule):
    r, orders = run(schedule)
    wrong = replace(schedule.base_profile, source_evidence='tampered base')
    r.provenance['cost_profile_sha256'] = wrong.sha256
    with pytest.raises(ContractViolation, match='P12_dated_cost_schedule'):
        validate(r, orders, schedule, wrong)


@pytest.mark.parametrize('timestamp', ['2025-01-09T10:00:00-05:00', '2025-01-08T10:00:00', None])
def test_missing_coverage_or_invalid_execution_timestamp_fails_closed(schedule, timestamp):
    r, orders = run(schedule)
    mutate(r, 'exec_ts', timestamp)
    with pytest.raises(ContractViolation, match='K02_ledger_cost_identities'):
        validate(r, orders, schedule)


@pytest.mark.parametrize('timestamp,day', [('2025-01-08T04:59:59Z','2025-01-07'),
                                         ('2025-01-08T05:00:00Z','2025-01-08')])
def test_new_york_effective_date_boundary(schedule, timestamp, day):
    r, orders = run(schedule)
    expected = schedule.profile_for(timestamp)
    assert expected.sha256 == schedule.profile_for(day+'T12:00:00-05:00').sha256
    mutate(r, 'exec_ts', timestamp)
    for key, value in [('cost_profile_sha256',expected.sha256), ('cost_profile_id',expected.profile_id),
                       ('cost_profile_version',expected.version)]:
        mutate(r, key, value)
    # These deliberately non-RTH rows fail T01, but K02 must use the exact Core NY boundary.
    with pytest.raises(ContractViolation) as error:
        validate(r, orders, schedule)
    assert 'K02_ledger_cost_identities' not in str(error.value)


def test_schedule_identity_and_missing_schedule_fail_closed(schedule):
    r, orders = run(schedule)
    with pytest.raises(ContractViolation, match='P12_dated_cost_schedule'):
        validate(r, orders, profile=schedule.base_profile)
    r.provenance['cost_schedule_sha256'] = 'wrong'
    with pytest.raises(ContractViolation, match='P12_dated_cost_schedule'):
        validate(r, orders, schedule)


def test_k02_uses_execution_date_even_with_conflicting_signal_date(schedule):
    r, orders = run(schedule)
    # Mutation isolates K02: the temporal contract must still reject these altered signal fields.
    r.fills.loc[r.fills.order_id == 'after', 'signal_time'] = pd.Timestamp('2025-01-07T10:02:00-05:00')
    assert schedule.profile_for(r.fills.iloc[1].signal_time).sha256 != r.fills.iloc[1].cost_profile_sha256
    with pytest.raises(ContractViolation) as error:
        validate(r, orders, schedule)
    assert 'T01_no_lookahead' in str(error.value)
    assert 'K02_ledger_cost_identities' not in str(error.value)


def test_execute_batch_forwards_pinned_schedule_to_contract(schedule, monkeypatch):
    from strategy_agent.research_only import core_phase4a as P
    from strategy_agent.signals.core import Decision
    from strategy_agent.signals import sma
    from market_research_core import tsm_research_costs
    # Inject two deterministic decisions only; the production SMA function is unchanged.
    decisions = [Decision(pd.Timestamp('2025-01-07T10:02:00-05:00'), 1, 'TSM', 'SYNTHETIC'),
                 Decision(pd.Timestamp('2025-01-08T10:02:00-05:00'), 0, 'TSM', 'SYNTHETIC')]
    monkeypatch.setattr(sma, 'sma_crossover', lambda *args: decisions)
    spec = json.loads((Path(__file__).parents[1] / 'specs/research/sma_crossover_tsm_15m_5_20_v0_1_1.json').read_text())
    schedules = {name:replace(schedule, scenario=name) for name in SCENARIOS}
    out = P.execute_batch(core, tsm_research_costs, frame=frame(),
        core_sha=A.core_identity()['git_sha'], dated_schedules=schedules, spec=spec,
        parameters={'fast':5, 'slow':20}, window_start='2025-01-07T00:00:00-05:00',
        window_end='2025-01-09T00:00:00-05:00', warmup_start='2025-01-07T00:00:00-05:00',
        dataset={'dataset_id':'SYNTHETIC_K02', 'snapshot_id':'SYNTHETIC_K02', 'producer_commit_sha':'synthetic'})
    assert out['orders'] == 2
    for item in out['scenarios'].values():
        assert len(item['result'].ledger) == 2
        assert item['m3a4_checks']['K02_ledger_cost_identities'] == 'PASS'
        assert item['m3a4_checks']['P12_dated_cost_schedule'] == 'PASS'
