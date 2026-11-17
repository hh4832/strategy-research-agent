"""P5C-TA-01 through the existing execute_batch -> pinned Core path. Synthetic prices, synthetic rates, synthetic
calendar days only (same pattern as tests/test_phase5c1_k02_dated_cost_identity.py). Skipped without the pinned Core."""
import copy
import json
from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
core = pytest.importorskip("market_research_core")
pytest.importorskip("market_research_core.dated_costs")
from market_research_core.dated_costs import DatedCostSchedule, SCENARIOS, rate_record_sha256, schedule_sha256  # noqa: E402

from strategy_agent.adapters import mrc_adapter as A  # noqa: E402
from strategy_agent.research.environment import synthetic_componentized_cost_profile  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / "tests/fixtures/p5c_ta01/ta01_spec_proposal.json").read_text(encoding="utf-8"))
PARAMS = {k: SPEC["parameters"]["values"][k]["default"] for k in ("res_n", "vol_m", "vol_k", "close_pos", "sup_l")}
DAYS = ("2025-01-06", "2025-01-07", "2025-01-08")
WINDOW_END = "2025-01-09T00:00:00-05:00"



@pytest.fixture
def schedule():
    base = A.core_cost_profile(synthetic_componentized_cost_profile(), instrument_type='EQUITY', tick_size=.01, core=core)
    added = ('commission_cap_rate', 'half_spread_bps', 'equity_commission_per_share',
             'exchange_fee_per_unit', 'clearing_fee_per_unit')
    base = replace(base, approval_status='DRAFT', equity_commission_rate=0, equity_commission_per_share=.005,
                   minimum_commission=1, commission_scheme='FIXED', commission_cap_rate=.01, half_spread_bps=0,
                   sell_tax_rate=0, slippage_ticks=0, exchange_fee_per_unit=0, clearing_fee_per_unit=0,
                   fee_rounding='HALF_UP', tax_rounding='HALF_UP',
                   component_basis={**base.component_basis, **{k: 'ASSUMED' for k in added}},
                   component_evidence={**base.component_evidence, **{k: 'SYNTHETIC' for k in added}},
                   component_approval_status={**base.component_approval_status, **{k: 'PENDING' for k in added}})

    def rate(kind, start, end, amount):
        r = dict(version='SYNTHETIC-' + kind + start, effective_from=start, effective_to=end, rate=amount,
                 unit='USD_PER_NOTIONAL' if kind == 'SEC' else 'USD_PER_SHARE',
                 source_ref='SYNTHETIC_FIXTURE: TA-01 engineering test', source_basis='ASSUMED',
                 verification_status='SYNTHETIC_FIXTURE', source_document_sha256=sha256(b'synthetic').hexdigest(),
                 record_sha256=None)
        r['record_sha256'] = rate_record_sha256(r)
        return r
    doc = dict(contract='tsm-dated-trading-costs-v1', schedule_id='SYNTHETIC-TA01', version='1.0.0',
               schedule_sha256=None, scope='RESEARCH_ONLY', evidence_kind='SYNTHETIC_FIXTURE',
               symbol='TSM', market='IBKR_EQUITY', timezone='America/New_York',
               coverage={'start': DAYS[0], 'end': DAYS[2]}, scenarios=copy.deepcopy(SCENARIOS),
               base_profile=asdict(base), base_profile_sha256=base.sha256,
               sec_rates=[rate('SEC', DAYS[0], DAYS[2], '0.00001')],
               taf_rates=[rate('TAF', DAYS[0], DAYS[2], '0.0002')])
    doc['schedule_sha256'] = schedule_sha256(doc)
    return DatedCostSchedule.from_document(doc, expected_sha256=doc['schedule_sha256'])


def frame(zero_volume_minute=None):
    """Core 15m conservative buckets from synthetic 1m rows. Days 1-2 flat (warm-up across sessions); day 3 has a
    breakout bucket (labels 14:00-14:14 -> decision 14:15) and then holds. ``zero_volume_minute`` ('HH:MM' on day 3)
    sets one source minute to volume 0, which makes its bucket ineligible in Core."""
    rows = []
    for d in DAYS:
        for t in pd.date_range(d + ' 09:30', periods=390, freq='min', tz='America/New_York'):
            hm = t.strftime('%H:%M')
            if d == DAYS[2] and '14:00' <= hm <= '14:14':
                o, h, lw, c, v = 100.0, 101.0, 99.9, 100.95, 3000.0
            elif d == DAYS[2] and hm >= '14:15':
                o, h, lw, c, v = 100.9, 101.0, 100.8, 100.9, 1000.0
            else:
                o, h, lw, c, v = 100.0, 100.2, 99.8, 100.0, 1000.0
            if d == DAYS[2] and hm == zero_volume_minute:
                v = 0.0
            rows.append(dict(source_timestamp=t, symbol='TSM', contract='TSM', open=o, high=h, low=lw, close=c, volume=v))
    return core.prepare_conservative_bars(pd.DataFrame(rows), bar_minutes=15)


def run(schedule, fr, spec=SPEC, params=None):
    from market_research_core import tsm_research_costs
    from strategy_agent.research_only import core_phase4a as P
    return P.execute_batch(core, tsm_research_costs, frame=fr, core_sha=A.core_identity()['git_sha'],
                           dated_schedules={n: replace(schedule, scenario=n) for n in SCENARIOS}, spec=spec,
                           parameters=dict(params or PARAMS), window_start=DAYS[0] + 'T00:00:00-05:00',
                           window_end=WINDOW_END, warmup_start=DAYS[0] + 'T00:00:00-05:00',
                           dataset={'dataset_id': 'SYNTHETIC_TA01', 'snapshot_id': 'SYNTHETIC_TA01',
                                    'producer_commit_sha': 'synthetic'})


def test_same_day_time_exit_signal_and_fill_through_core(schedule, monkeypatch):
    from strategy_agent.signals import sma

    def forbidden(*a, **k):
        raise AssertionError("TA-01 must not reach the SMA signal")
    monkeypatch.setattr(sma, 'sma_crossover', forbidden)
    out = run(schedule, frame())
    assert out['signal_family']['family'] == 'TA01'
    got = [(pd.Timestamp(ts).tz_convert('America/New_York').strftime('%m-%d %H:%M'), tgt, r) for ts, tgt, r in out['decisions']]
    assert got == [('01-08 14:15', 1, 'TA01_ENTRY'), ('01-08 15:15', 0, 'TIME_FLATTEN')]
    assert out['orders'] == 2
    for item in out['scenarios'].values():
        assert item['ta01_intraday_check']['status'] == 'PASS'
        trip = item['result'].round_trips.iloc[0]
        entry = pd.Timestamp(trip['entry_ts']).tz_convert('America/New_York')
        exit_ = pd.Timestamp(trip['exit_ts']).tz_convert('America/New_York')
        # Core fills at the first eligible bucket with bar_start strictly after the decision: labels 14:30 / 15:30
        assert (entry.strftime('%m-%d %H:%M'), exit_.strftime('%m-%d %H:%M')) == ('01-08 14:29', '01-08 15:29')
        assert len(item['result'].open_positions) == 0


def test_exit_fill_bucket_ineligible_fails_whole_execution(schedule):
    from strategy_agent.research_only.core_phase4a import TA01NoOvernightViolation
    with pytest.raises(TA01NoOvernightViolation, match='no-overnight violated'):
        run(schedule, frame(zero_volume_minute='15:35'))      # the only same-day fill bucket (15:30) is ineligible


def test_no_same_day_exit_decision_fails_whole_execution(schedule):
    from strategy_agent.research_only.core_phase4a import TA01NoOvernightViolation
    with pytest.raises(TA01NoOvernightViolation, match='NO_SAME_DAY_EXIT'):
        run(schedule, frame(zero_volume_minute='15:05'))      # the 15:15 decision bucket is ineligible


def test_sma_spec_with_ta01_parameters_is_refused_before_any_order(schedule):
    from strategy_agent.research_only import core_phase4a as P
    sma_spec = json.loads((ROOT / 'specs/research/sma_crossover_tsm_15m_5_20_v0_1_1.json').read_text())
    with pytest.raises(P.Phase4ABindingError):
        run(schedule, frame(), spec=sma_spec)
