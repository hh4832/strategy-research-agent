"""P5C-TA-01 through the existing execute_batch -> pinned Core path. Synthetic prices, synthetic rates, synthetic
calendar days only (same pattern as tests/test_phase5c1_k02_dated_cost_identity.py). Skipped without the pinned Core."""
import copy
import json
from dataclasses import asdict, replace
from types import SimpleNamespace
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


def source(zero_volume=(), missing=()):
    """Synthetic 1m source rows in Core's loader format. Days 1-2 flat (warm-up across sessions); day 3 has a
    breakout 15m bucket (labels 14:00-14:14 -> decision 14:15) and then holds. ``zero_volume`` / ``missing``:
    'HH:MM' minutes on day 3 set to volume 0 / removed from the source."""
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
            rows.append(dict(source_timestamp=t, symbol='TSM', contract='TSM', open=o, high=h, low=lw, close=c, volume=v))
    return pd.DataFrame(rows)


def frame(zero_volume=(), missing=(), bar_minutes=1):
    """Core conservative frame (1m = execution bars) from the synthetic source."""
    return core.prepare_conservative_bars(source(zero_volume, missing), bar_minutes=bar_minutes)


def loaded(**kw):
    """What Core's loader returns: the 1m source rows plus Core's own 15m conservative frame."""
    src = source(**kw)
    return SimpleNamespace(native=src.copy(), source_1m=src,
                           conservative_15m=core.prepare_conservative_bars(src, bar_minutes=15),
                           provenance={'scope': 'SYNTHETIC_TA01'})


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


def test_15m_signal_fills_on_1m_bar_within_2_minutes(schedule, monkeypatch):
    from strategy_agent.signals import sma

    def forbidden(*a, **k):
        raise AssertionError("TA-01 must not reach the SMA signal")
    monkeypatch.setattr(sma, 'sma_crossover', forbidden)
    out = run(schedule, frame())
    assert out['signal_family']['family'] == 'TA01' and out['signal_family']['execution_bars_minutes'] == 1
    assert [(ny(ts), tgt, r) for ts, tgt, r in out['decisions']] == [('01-08 14:15', 1, 'TA01_ENTRY'),
                                                                     ('01-08 15:15', 0, 'TIME_FLATTEN')]
    assert out['orders'] == 2 and out['ta01_fill_window']['not_submitted'] == []
    for item in out['scenarios'].values():
        assert item['ta01_intraday_check']['status'] == 'PASS'
        trip = item['result'].round_trips.iloc[0]
        # first 1m bar strictly after the decision: source label 14:17 (bar_start 14:16, opens by 14:17) and 15:17
        assert (ny(trip['entry_ts']), ny(trip['exit_ts'])) == ('01-08 14:16', '01-08 15:16')
        assert len(item['result'].open_positions) == 0


def test_no_bar_within_2_minutes_means_no_fill(schedule):
    out = run(schedule, frame(missing=('14:17',)))           # next 1m bar after 14:15 opens at 14:18 -> outside window
    assert [d['reason'] for d in out['ta01_fill_window']['not_submitted']] == ['FILL_WINDOW_NO_BAR']
    for item in out['scenarios'].values():
        assert len(item['result'].round_trips) == 0 and len(item['result'].open_positions) == 0
        assert item['ta01_intraday_check']['status'] == 'PASS'


def test_untradable_bar_inside_window_means_no_fill(schedule):
    out = run(schedule, frame(zero_volume=('14:17',)))       # in-window bar has no volume -> Core rejects, no retry
    assert out['ta01_fill_window']['not_submitted'] == []
    for item in out['scenarios'].values():
        assert len(item['result'].round_trips) == 0 and len(item['result'].open_positions) == 0
        assert 'ZERO_VOLUME_BAR' in set(item['result'].rejections['reason'])


def test_exit_not_filled_same_day_fails_whole_execution(schedule):
    from strategy_agent.research_only.core_phase4a import TA01NoOvernightViolation
    result = None
    with pytest.raises(TA01NoOvernightViolation, match='no-overnight violated'):
        result = run(schedule, frame(zero_volume=('15:17',)))   # the exit's only in-window bar is untradable
    assert result is None                                    # no metrics, trades or report are returned


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


# ------------------------------------------------------------------ governed runner wiring (synthetic only)

SMA_SPEC_PATH = 'specs/research/sma_crossover_tsm_15m_5_20_v0_1_1.json'
TA01_SPEC_PATH = 'tests/fixtures/p5c_ta01/ta01_spec_proposal.json'


def test_execution_frame_sma_keeps_loaded_15m_object():
    from strategy_agent.research_only import core_phase4a as P
    ld = loaded()
    sma_spec = json.loads((ROOT / SMA_SPEC_PATH).read_text())
    fr, info = P.execution_frame(core, ld, sma_spec, {'fast': 5, 'slow': 20})
    assert fr is ld.conservative_15m and info is None


def test_execution_frame_ta01_is_core_1m_and_matches_direct_build():
    from strategy_agent.research_only import core_phase4a as P
    ld = loaded()
    fr, info = P.execution_frame(core, ld, SPEC, PARAMS)
    assert set(fr['bar_minutes']) == {1} and info['execution_bars_minutes'] == 1
    assert info['loaded_15m_cross_check'] == 'PASS'
    pd.testing.assert_frame_equal(fr.drop(columns=['source_rows']), frame().drop(columns=['source_rows']))


@pytest.mark.parametrize('tamper', ['drop_bucket', 'flip_eligibility'])
def test_execution_frame_refuses_loaded_15m_that_core_would_not_produce(tamper):
    from strategy_agent.research_only import core_phase4a as P
    ld = loaded()
    if tamper == 'drop_bucket':
        ld.conservative_15m = ld.conservative_15m.iloc[1:].reset_index(drop=True)
    else:
        i = ld.conservative_15m.index[ld.conservative_15m['signal_eligible']][0]
        ld.conservative_15m.loc[i, 'signal_eligible'] = False
    with pytest.raises(P.Phase4ABindingError, match='rebuild'):
        P.execution_frame(core, ld, SPEC, PARAMS)


def _runner_execute(tmp_path, schedule, monkeypatch, spec_path, params, ld):
    """Calls the governed runner's _execute with the data load replaced by synthetic Core output. Preflight, the
    authorization guard, slot consumption and real data are NOT involved (they run before _execute)."""
    from market_research_core import tsm_research_costs
    from strategy_agent.research_only import core_phase4a as P
    from strategy_agent.research_only.runner import ResearchOnlyRunner, write_once
    seen = {}
    monkeypatch.setattr(P, 'load_continuous_native', lambda *a, **k: ld)
    monkeypatch.setattr(P.SnapshotBinding, 'from_config', classmethod(lambda cls, cfg: SimpleNamespace(
        manifest_relative_path='manifest.json')))
    real = P.execute_batch

    def spy(*a, **k):
        seen['bar_minutes'] = set(k['frame']['bar_minutes'])
        return real(*a, **k)
    monkeypatch.setattr(P, 'execute_batch', spy)
    (tmp_path / 'snap').mkdir()
    (tmp_path / 'snap' / 'manifest.json').write_text('{"synthetic": true}')
    spec = json.loads((ROOT / spec_path).read_text())
    cfg = {'strategy': {'spec_path': spec_path, 'parameters': params, 'strategy_id': spec['strategy_id'],
                        'version': spec.get('version', '0')},
           'windows': {'warmup_start': DAYS[0] + 'T00:00:00-05:00', 'start': DAYS[0] + 'T00:00:00-05:00',
                       'end': WINDOW_END},
           'dataset': {'dataset_id': 'SYNTHETIC_TA01', 'snapshot_id': 'SYNTHETIC_TA01', 'producer_commit_sha': 'synthetic'},
           'sample': {'min_trades': 1}, 'snapshot_binding': {}, 'provenance': {'config_sha256': 'synthetic'},
           'authorization_id': 'SYNTHETIC_NONE', 'temporal_policy': {'sha256': 'synthetic'}}
    dc = {'snapshot_root': tmp_path / 'snap', 'dates': (DAYS[0], DAYS[2]), 'research_window': (DAYS[0], DAYS[2]),
          'holdout': (), 'dated_schedules': {n: replace(schedule, scenario=n) for n in SCENARIOS}}
    out = ResearchOnlyRunner(root=ROOT)._execute(
        cfg, '20261118_000000_abcdef0', {'synthetic': True}, tmp_path / 'archive', dc,
        {'core_git_sha': A.core_identity()['git_sha']}, {}, core, tsm_research_costs,
        SimpleNamespace(DateWindow=None), run_context={'agent_git_sha': 'synthetic'})
    assert write_once  # archive files are written with the runner's O_EXCL writer
    return out, seen, json.loads((tmp_path / 'archive' /
                                  'execution_diagnostics.json').read_text())


def test_runner_passes_core_1m_frame_for_ta01_and_archives_fill_window(tmp_path, schedule, monkeypatch):
    out, seen, diag = _runner_execute(tmp_path, schedule, monkeypatch, TA01_SPEC_PATH, PARAMS, loaded())
    assert out['executed'] and seen['bar_minutes'] == {1}
    assert diag['execution_frame']['execution_bars_minutes'] == 1
    assert diag['ta01_fill_window']['window_minutes'] == 2 and diag['ta01_fill_window']['not_submitted'] == []
    assert diag['signal_family']['family'] == 'TA01'
    import csv
    with open(tmp_path / 'archive' / 'trades.csv', newline='') as fh:
        trades = list(csv.DictReader(fh))
    assert len(trades) == len(SCENARIOS) and {t['overnight'] for t in trades} == {'False'}   # one intraday trade each


def test_runner_ta01_unfilled_exit_writes_no_archive(tmp_path, schedule, monkeypatch):
    from strategy_agent.research_only.core_phase4a import TA01NoOvernightViolation
    with pytest.raises(TA01NoOvernightViolation):
        _runner_execute(tmp_path, schedule, monkeypatch, TA01_SPEC_PATH, PARAMS, loaded(zero_volume=('15:17',)))
    assert not (tmp_path / 'archive').exists()          # run() would write only FAILURE.json


def test_runner_sma_still_gets_loaded_15m_and_archive_has_no_ta01_keys(tmp_path, schedule, monkeypatch):
    out, seen, diag = _runner_execute(tmp_path, schedule, monkeypatch, SMA_SPEC_PATH, {'fast': 5, 'slow': 20},
                                      loaded())
    assert out['executed'] and seen['bar_minutes'] == {15}
    assert not {'execution_frame', 'ta01_fill_window', 'signal_family'} & set(diag)
