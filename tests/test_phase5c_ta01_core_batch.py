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
    return make_schedule()


def make_schedule(cap=None):
    base = A.core_cost_profile(synthetic_componentized_cost_profile(), instrument_type='EQUITY', tick_size=.01, core=core)
    added = ('commission_cap_rate', 'half_spread_bps', 'equity_commission_per_share',
             'exchange_fee_per_unit', 'clearing_fee_per_unit')
    if cap is not None:
        added += ('volume_participation_cap',)
    base = replace(base, approval_status='DRAFT', equity_commission_rate=0, equity_commission_per_share=.005,
                   minimum_commission=1, commission_scheme='FIXED', commission_cap_rate=.01, half_spread_bps=0,
                   sell_tax_rate=0, slippage_ticks=0, exchange_fee_per_unit=0, clearing_fee_per_unit=0,
                   volume_participation_cap=cap,
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


def source(zero_volume=(), missing=(), volumes=None):
    """Synthetic 1m source rows in Core's loader format. Days 1-2 flat (warm-up across sessions); day 3 has a
    breakout 15m bucket (labels 14:00-14:14 -> decision 14:15) and then holds. ``zero_volume`` / ``missing`` /
    ``volumes``: 'HH:MM' minutes on day 3 set to volume 0 / removed / given an explicit volume."""
    rows = []
    for d in DAYS:
        for t in pd.date_range(d + ' 09:30', periods=390, freq='min', tz='America/New_York'):
            hm = t.strftime('%H:%M')
            if d == DAYS[2] and hm in missing:
                continue
            if d == DAYS[2] and '14:00' <= hm <= '14:14':
                o, h, lw, c, v = 100.0, 101.0, 99.9, 100.95, 3000.0
            elif d == DAYS[2] and hm >= '14:15':
                o, h, lw, c, v = 100.9, 101.0, 100.8, 100.9, 1000.0
            else:
                o, h, lw, c, v = 100.0, 100.2, 99.8, 100.0, 1000.0
            if d == DAYS[2] and hm in zero_volume:
                v = 0.0
            if d == DAYS[2] and volumes and hm in volumes:
                v = float(volumes[hm])
            rows.append(dict(source_timestamp=t, symbol='TSM', contract='TSM', open=o, high=h, low=lw, close=c, volume=v))
    return pd.DataFrame(rows)


def frame(zero_volume=(), missing=(), bar_minutes=1, volumes=None):
    """Core conservative frame (1m = execution bars) from the synthetic source."""
    return core.prepare_conservative_bars(source(zero_volume, missing, volumes), bar_minutes=bar_minutes)


def run(schedule, fr, spec=SPEC, params=None):
    from market_research_core import tsm_research_costs
    from strategy_agent.research_only import core_phase4a as P
    return P.execute_batch(core, tsm_research_costs, frame=fr, core_sha=A.core_identity()['git_sha'],
                           dated_schedules={n: replace(schedule, scenario=n) for n in SCENARIOS}, spec=spec,
                           parameters=dict(params or PARAMS), window_start=DAYS[0] + 'T00:00:00-05:00',
                           window_end=WINDOW_END, warmup_start=DAYS[0] + 'T00:00:00-05:00',
                           dataset={'dataset_id': 'SYNTHETIC_TA01', 'snapshot_id': 'SYNTHETIC_TA01',
                                    'producer_commit_sha': 'synthetic'})


def ny(ts):
    return pd.Timestamp(ts).tz_convert('America/New_York').strftime('%m-%d %H:%M')


def trips(out):
    return {n: tuple((ny(t['entry_ts']), ny(t['exit_ts'])) for t in item['result'].round_trips.to_dict('records'))
            for n, item in out['scenarios'].items()}


def test_15m_signal_fills_on_first_1m_bar_in_window(schedule, monkeypatch):
    from strategy_agent.signals import sma

    def forbidden(*a, **k):
        raise AssertionError("TA-01 must not reach the SMA signal")
    monkeypatch.setattr(sma, 'sma_crossover', forbidden)
    out = run(schedule, frame())
    assert out['signal_family']['family'] == 'TA01' and out['signal_family']['execution_bars_minutes'] == 1
    assert [(ny(ts), tgt, r) for ts, tgt, r in out['decisions']] == [('01-08 14:15', 1, 'TA01_ENTRY'),
                                                                     ('01-08 15:15', 0, 'TIME_FLATTEN')]
    fw = out['ta01_fill_window']
    assert out['orders'] == 2 and fw['not_submitted'] == [] and fw['order_time_shifted'] == 0
    for item in out['scenarios'].values():
        assert item['ta01_intraday_check']['status'] == 'PASS' and len(item['result'].open_positions) == 0
    # first in-window bar: label 14:17 -> bar_start (earliest possible open) 14:16, inside (14:15, 14:17]
    assert set(trips(out).values()) == {(('01-08 14:16', '01-08 15:16'),)}


def test_missing_first_bar_waits_for_next_bar_in_window(schedule):
    out = run(schedule, frame(missing=('14:17',)))          # label 14:18: bar_start 14:17 = t+2 (boundary included)
    assert out['ta01_fill_window']['not_submitted'] == [] and out['ta01_fill_window']['order_time_shifted'] == 0
    assert set(trips(out).values()) == {(('01-08 14:17', '01-08 15:16'),)}


def test_zero_volume_first_bar_waits_for_next_bar_in_window(schedule):
    out = run(schedule, frame(zero_volume=('14:17',)))
    assert out['ta01_fill_window']['order_time_shifted'] == 1    # chosen bar made Core's first candidate
    assert set(trips(out).values()) == {(('01-08 14:17', '01-08 15:16'),)}
    for item in out['scenarios'].values():
        rej = item['result'].rejections
        assert len(rej) == 0 or 'ZERO_VOLUME_BAR' not in set(rej['reason'])


@pytest.mark.parametrize('kw', [{'missing': ('14:17', '14:18')}, {'zero_volume': ('14:17', '14:18')},
                                {'missing': ('14:17',), 'zero_volume': ('14:18',)}])
def test_no_tradable_bar_within_window_means_no_entry(schedule, kw):
    out = run(schedule, frame(**kw))                          # next tradable bar would open at 14:18 -> too late
    ns = out['ta01_fill_window']['not_submitted']
    assert [d['reason'] for d in ns[:1]] == ['FILL_WINDOW_NO_TRADABLE_BAR']
    assert ns[0]['missing_minutes'] + ns[0]['zero_volume_minutes'] == 2
    for item in out['scenarios'].values():
        assert len(item['result'].round_trips) == 0 and len(item['result'].open_positions) == 0
        assert item['ta01_intraday_check']['status'] == 'PASS'


def test_exit_without_tradable_bar_in_window_fails_whole_execution(schedule):
    from strategy_agent.research_only.core_phase4a import TA01NoOvernightViolation
    result = None
    with pytest.raises(TA01NoOvernightViolation, match='no-overnight violated'):
        result = run(schedule, frame(zero_volume=('15:17', '15:18')))
    assert result is None                                    # no metrics, trades or report are returned


def test_known_volume_cap_blocks_order_before_submission():
    # known volume at 14:15 is the 14:14 minute (3000); at 15:15 the 15:14 minute (1000)
    out = run(make_schedule(cap=0.02), frame())               # 3000 x 0.02 = 60 < 100 -> entry not submitted
    assert out['ta01_fill_window']['not_submitted'][0]['reason'] == 'KNOWN_VOLUME_CAP'
    assert all(len(i['result'].round_trips) == 0 for i in out['scenarios'].values())
    out = run(make_schedule(cap=0.05), frame(volumes={'15:14': 3000}))   # 150 >= 100 both
    assert out['ta01_fill_window']['not_submitted'] == []


def test_core_full_minute_cap_still_applies_after_known_volume_check():
    # known volume passes (3000 x 0.05 = 150), but the chosen bar's FULL-minute volume is 100 -> Core rejects with its
    # own pinned cap (not changed here); NEXT_BAR: no fill. Documented residual; counted by the fill diagnostic.
    out = run(make_schedule(cap=0.05), frame(volumes={'14:17': 100, '15:14': 3000}))
    for item in out['scenarios'].values():
        assert len(item['result'].round_trips) == 0
        assert 'VOLUME_PARTICIPATION_EXCEEDED' in set(item['result'].rejections['reason'])


def test_delayed_open_is_recorded_and_its_buckets_excluded(schedule):
    early = tuple(f"09:{m:02d}" for m in range(30, 45))       # nothing trades until 09:45 on day 3
    out = run(schedule, frame(zero_volume=early))
    info = out['ta01_delayed_open']['sessions'][DAYS[2]]
    assert info['first_traded_label'].startswith(DAYS[2] + 'T09:45') and DAYS[0] not in out['ta01_delayed_open']['sessions']
    assert info['exclude_bucket_first_labels_through'].startswith(DAYS[2] + 'T09:45')
    assert [r for _, _, r in out['decisions']] == ['TA01_ENTRY', 'TIME_FLATTEN']


def test_no_feasible_exit_decision_fails_whole_execution(schedule):
    from strategy_agent.research_only.core_phase4a import TA01NoOvernightViolation
    result = None
    with pytest.raises(TA01NoOvernightViolation, match='NO_SAME_DAY_EXIT'):
        result = run(schedule, frame(zero_volume=('15:05', '15:20')))   # 15:15 and 15:30 decision buckets ineligible
    assert result is None


def test_ta01_requires_1m_execution_frame(schedule):
    from strategy_agent.research_only import core_phase4a as P
    with pytest.raises(P.Phase4ABindingError, match='1m'):
        run(schedule, frame(bar_minutes=15))


def test_sma_spec_with_ta01_parameters_is_refused_before_any_order(schedule):
    from strategy_agent.research_only import core_phase4a as P
    sma_spec = json.loads((ROOT / 'specs/research/sma_crossover_tsm_15m_5_20_v0_1_1.json').read_text())
    with pytest.raises(P.Phase4ABindingError):
        run(schedule, frame(), spec=sma_spec)
