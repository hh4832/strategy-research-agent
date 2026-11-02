import math

import numpy as np
import pytest

from strategy_agent.evaluation import metrics as M
from strategy_agent.evaluation import splits as S


# -------------------------------------------------------------- metrics


def test_sharpe_hand_value():
    r = [0.01, 0.02, -0.01, 0.03, 0.0]
    mean, sd = 0.01, math.sqrt(0.00025)                 # sample var = 0.001/4
    assert M.sharpe_ratio(r) == pytest.approx(mean / sd)
    assert M.sharpe_ratio(r, 252) == pytest.approx(mean / sd * math.sqrt(252))


def test_sortino_hand_value():
    r = [0.02, -0.01, 0.03, -0.02]
    dd = math.sqrt((0.01 ** 2 + 0.02 ** 2) / 4)
    assert M.sortino_ratio(r) == pytest.approx(0.005 / dd)


def test_max_drawdown_and_duration():
    eq = [100, 120, 90, 130, 65, 70]
    assert M.max_drawdown(eq) == pytest.approx(-0.5)
    assert M.max_drawdown_duration(eq) == 2


def test_profit_factor_exposure_turnover():
    assert M.profit_factor([100, -50, 30, -30]) == pytest.approx(130 / 80)
    assert M.exposure([0, 1, 1, 0, -1]) == pytest.approx(0.6)
    assert M.turnover([1000, 1000, 500, 500], average_equity=1000, years=1) == pytest.approx(1.5)


def test_psr_half_at_benchmark_and_monotone():
    rng = np.random.default_rng(1)
    r = rng.normal(0.001, 0.01, 1000)
    sr = M.sharpe_ratio(r)
    assert M.probabilistic_sharpe_ratio(r, sr) == pytest.approx(0.5)
    assert M.probabilistic_sharpe_ratio(r, 0.0) > M.probabilistic_sharpe_ratio(r, sr / 2)


def test_expected_max_sharpe_grows_with_trials():
    vals = [M.expected_max_sharpe(n, 0.01) for n in (1, 10, 100, 1000)]
    assert vals[0] == 0 and all(a < b for a, b in zip(vals, vals[1:]))


def test_dsr_penalises_more_trials():
    rng = np.random.default_rng(7)
    r = rng.normal(0.002, 0.01, 750)
    assert M.deflated_sharpe_ratio(r, 1, 0.005) > M.deflated_sharpe_ratio(r, 100, 0.005) > \
        M.deflated_sharpe_ratio(r, 10_000, 0.005)


def test_mintrl_finite_and_infinite():
    rng = np.random.default_rng(3)
    good = rng.normal(0.003, 0.01, 500)
    assert 1 < M.minimum_track_record_length(good) < 500
    assert M.minimum_track_record_length(-good) == math.inf


def test_holm_and_bh_known_values():
    p = [0.01, 0.04, 0.03, 0.005]
    h = M.holm(p)
    # sorted 0.005,0.01,0.03,0.04 -> 0.02,0.03,0.06,0.06
    assert h.adjusted_p == pytest.approx((0.03, 0.06, 0.06, 0.02))
    assert h.reject == (True, False, False, True)
    bh = M.benjamini_hochberg(p)
    # ranked p*m/i: 0.02,0.02,0.04,0.04 -> step-up min
    assert bh.adjusted_p == pytest.approx((0.02, 0.04, 0.04, 0.02))
    by = M.benjamini_hochberg(p, dependent=True)
    assert all(b >= a for a, b in zip(bh.adjusted_p, by.adjusted_p))


def test_pbo_noise_vs_signal():
    rng = np.random.default_rng(11)
    noise = rng.normal(0, 0.01, (800, 20))
    pbo_noise = M.probability_of_backtest_overfitting(noise, 8)
    signal = noise.copy()
    signal[:, 0] += 0.004                       # one genuinely better config
    pbo_signal = M.probability_of_backtest_overfitting(signal, 8)
    assert pbo_signal < 0.1 < pbo_noise


def test_cost_sensitivity():
    out = M.cost_sensitivity([100, 50, -20], [10, 10, 10], [0, 1, 2, 3])
    assert out["net_by_multiplier"] == {0.0: 130.0, 1.0: 100.0, 2.0: 70.0, 3.0: 40.0}
    assert out["breakeven_multiplier"] == pytest.approx(130 / 30)


# ---------------------------------------------------------------- splits


def _labels(n, horizon):
    t0 = np.arange(n, dtype=float)
    return t0, t0 + horizon


def test_chronological_split_is_ordered_and_disjoint():
    tr, va, te = S.chronological_split(100, (0.6, 0.2, 0.2), gap=3)
    assert tr.max() < va.min() and va.max() < te.min()
    assert va.min() - tr.max() == 4 and len(te) == 17


def test_walk_forward_purges_overlapping_labels():
    t0, t1 = _labels(200, horizon=5)
    folds = S.walk_forward(200, t0, t1, train_size=100, test_size=20)
    assert len(folds) == 5
    for f in folds:
        S.assert_no_leakage(f, t0, t1)
        assert f.train.max() < f.test.min()
        assert len(f.purged) == 5                          # last 5 labels resolve inside test
    anchored = S.walk_forward(200, t0, t1, 100, 20, anchored=True)
    assert all(f.train.min() == 0 for f in anchored)


def test_purged_kfold_embargo():
    t0, t1 = _labels(100, horizon=2)
    folds = S.purged_kfold(100, t0, t1, k=5, embargo=3)
    mid = folds[2]                                         # test 40..59, labels span [40, 61]
    S.assert_no_leakage(mid, t0, t1)
    # 38,39 resolve inside the window; 60,61 START inside it -> both purged
    assert set(mid.purged.tolist()) == {38, 39, 60, 61}
    # embargo counted from the last test label end (61): t0 in (61, 64]
    assert set(mid.embargoed.tolist()) == {62, 63, 64}


def test_assert_no_leakage_detects_leak():
    t0, t1 = _labels(50, horizon=10)
    bad = S.Fold(np.arange(0, 30), np.arange(30, 40), np.array([]), np.array([]))
    with pytest.raises(AssertionError):
        S.assert_no_leakage(bad, t0, t1)
