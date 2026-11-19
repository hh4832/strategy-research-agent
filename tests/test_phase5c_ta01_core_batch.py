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
    assert diag['signal_family']['family'] == 'TA01' and 'ta01_delayed_open' in diag
    assert all(v['status'] == 'PASS' for v in diag['temporal'].values())
    import csv
    with open(tmp_path / 'archive' / 'trades.csv', newline='') as fh:
        trades = list(csv.DictReader(fh))
    assert len(trades) == len(SCENARIOS) and {t['overnight'] for t in trades} == {'False'}   # one intraday trade each


def test_runner_ta01_unfilled_exit_writes_no_archive(tmp_path, schedule, monkeypatch):
    from strategy_agent.research_only.core_phase4a import TA01NoOvernightViolation
    with pytest.raises(TA01NoOvernightViolation):
        _runner_execute(tmp_path, schedule, monkeypatch, TA01_SPEC_PATH, PARAMS, loaded(zero_volume=('15:17', '15:18')))
    assert not (tmp_path / 'archive').exists()          # run() would write only FAILURE.json


def test_runner_sma_still_gets_loaded_15m_and_archive_has_no_ta01_keys(tmp_path, schedule, monkeypatch):
    out, seen, diag = _runner_execute(tmp_path, schedule, monkeypatch, SMA_SPEC_PATH, {'fast': 5, 'slow': 20},
                                      loaded())
    assert out['executed'] and seen['bar_minutes'] == {15}
    assert not {'execution_frame', 'ta01_fill_window', 'signal_family'} & set(diag)


def test_runner_archive_accepts_shifted_order_time_within_window_only(tmp_path, schedule, monkeypatch):
    out, seen, diag = _runner_execute(tmp_path, schedule, monkeypatch, TA01_SPEC_PATH, PARAMS,
                                      loaded(zero_volume=('14:17',)))       # entry fills on the second in-window bar
    assert diag['ta01_fill_window']['order_time_shifted'] == 1
    for t in diag['temporal'].values():
        assert t['status'] == 'PASS' and t['order_time_equals_signal_time'] is False
        assert 0 < t['max_minutes_signal_to_order_time'] <= 2


# ------------------------------------------------------------------ fill diagnostic (synthetic only; counts, no PnL)

def diag(fr, cap=None, spec=SPEC):
    from strategy_agent.research_only import ta01_fill_diagnostic as D
    return D.diagnose(core, fr, spec=spec, parameters=PARAMS, participation_cap=cap,
                      window_start=DAYS[0] + 'T00:00:00-05:00', window_end=WINDOW_END)


def test_diagnostic_counts_match_execute_batch_on_the_same_frame(schedule):
    fr = frame()
    d = diag(fr)
    assert (d['entry']['intents'], d['entry']['fillable'], d['entry']['chosen_first_candidate']) == (1, 1, 1)
    assert (d['exit']['intents'], d['exit']['fillable'], d['exit_failures']['count']) == (1, 1, 0)
    out = run(schedule, fr)
    assert all(len(i['result'].round_trips) == d['exit']['fillable'] for i in out['scenarios'].values())


@pytest.mark.parametrize('kw,side,key,cause', [
    ({'zero_volume': ('14:17',)}, 'entry', 'chosen_later_candidate_waited', None),
    ({'missing': ('14:17', '14:18')}, 'entry', 'no_tradable_bar_in_window', 'ALL_MINUTES_MISSING'),
    ({'zero_volume': ('14:17', '14:18')}, 'entry', 'no_tradable_bar_in_window', 'ALL_MINUTES_ZERO_VOLUME'),
    ({'missing': ('14:17',), 'zero_volume': ('14:18',)}, 'entry', 'no_tradable_bar_in_window', 'MISSING_AND_ZERO_VOLUME'),
])
def test_diagnostic_window_outcomes(kw, side, key, cause):
    d = diag(frame(**kw))
    assert d[side][key] == 1
    if cause:
        assert d[side]['no_tradable_bar_causes'] == {cause: 1} and d['exit']['intents'] == 0   # flat: no exit order


def test_diagnostic_exit_failure_is_counted_not_raised():
    d = diag(frame(zero_volume=('15:17', '15:18')))
    assert d['exit']['no_tradable_bar_in_window'] == 1 and d['exit_failures']['count'] == 1


def test_diagnostic_participation_known_volume_and_core_residual():
    d = diag(frame(), cap=0.02)                                 # known 3000 x 0.02 = 60 < 100
    assert d['entry']['known_volume_cap_blocked'] == 1 and d['exit']['intents'] == 0
    assert d['entry']['known_volume_to_quantity']['median'] == 30.0
    d = diag(frame(volumes={'14:17': 100, '15:14': 3000}), cap=0.05)
    assert d['entry']['core_full_minute_cap_would_reject'] == 1 and d['entry']['fillable'] == 0


def test_diagnostic_reports_delayed_open_and_data_summary_without_performance_fields():
    from strategy_agent.research_only import ta01_fill_diagnostic as D
    d = diag(frame(zero_volume=tuple(f"09:{m:02d}" for m in range(30, 45))))
    assert d['delayed_open']['sessions'] == 1 and d['delayed_open']['dates'] == [DAYS[2]]
    assert d['data']['rth_sessions'] == 3 and 2025 in d['data']['median_daily_rth_volume_by_year']
    D.assert_no_performance_fields(d)


# ------------------------------------------------------------------ P5C-TA01-Q8 (8 units; rules identical)

Q8 = json.loads((ROOT / "tests/fixtures/p5c_ta01/ta01_q8_spec_variant.json").read_text(encoding="utf-8"))


def test_q8_decisions_and_fill_times_equal_ta01_only_quantity_differs(schedule):
    fr = frame()
    base, q8 = run(schedule, fr), run(schedule, fr, spec=Q8)
    assert q8['decisions'] == base['decisions'] and q8['orders'] == base['orders']
    assert q8['ta01_fill_window'] == base['ta01_fill_window']
    assert q8['ta01_delayed_open'] == base['ta01_delayed_open']
    for name in SCENARIOS:
        fb, fq = base['scenarios'][name]['result'].fills, q8['scenarios'][name]['result'].fills
        assert list(fb['exec_ts']) == list(fq['exec_ts']) and list(fb['side']) == list(fq['side'])
        assert set(fb['qty'].abs()) == {100} and set(fq['qty'].abs()) == {8}
        rb, rq = base['scenarios'][name]['result'].round_trips, q8['scenarios'][name]['result'].round_trips
        assert list(rb['entry_ts']) == list(rq['entry_ts']) and list(rb['exit_ts']) == list(rq['exit_ts'])


def test_q8_minimum_minute_volume_is_800_at_one_percent():
    # known volume at 14:15 is the 14:14 minute; at 15:15 the 15:14 minute (default 1000 >= 800)
    d = diag(frame(volumes={'14:14': 800}), cap=0.01, spec=Q8)
    assert d['definitions']['min_minute_volume_for_quantity'] == 800
    assert d['entry']['fillable'] == 1 and d['exit']['fillable'] == 1
    d = diag(frame(volumes={'14:14': 799}), cap=0.01, spec=Q8)
    assert d['entry']['known_volume_cap_blocked'] == 1 and d['exit']['intents'] == 0
    d = diag(frame(volumes={'14:17': 799}), cap=0.01, spec=Q8)       # known passes; Core full-minute check would not
    assert d['entry']['core_full_minute_cap_would_reject'] == 1
    d = diag(frame(), cap=0.01)                                      # same frame at 100 units: needs 10,000
    assert d['entry']['known_volume_cap_blocked'] == 1 and d['definitions']['min_minute_volume_for_quantity'] == 10000


def test_q8_diagnostic_reports_variant_labels_and_open_core_blocker():
    d = diag(frame(), cap=0.01, spec=Q8)
    assert d['variant'] == {'strategy_id': Q8['strategy_id'], 'variant': 'P5C-TA01-Q8', 'units': 8,
                            'labels': ['POST_DIAGNOSTIC_VARIANT', 'EXPLORATORY_ONLY'],
                            'parent_strategy_id': SPEC['strategy_id']}
    assert [b['status'] for b in d['open_blockers']] == ['BLOCKER']
    assert d['open_blockers'][0]['id'] == 'CORE_FULL_MINUTE_CAP_LOOKAHEAD'
