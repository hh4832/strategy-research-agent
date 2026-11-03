import copy
import json
from pathlib import Path

from strategy_agent.family.canonical import FamilyIndex, canonical_structure, structure_hash

EX = Path(__file__).parents[1] / "specs" / "examples"
SMA = json.loads((EX / "sma_crossover_tw_2330_5m.json").read_text())
ORB = json.loads((EX / "orb_txf_1m.json").read_text())
ATR = json.loads((EX / "atr_breakout_tsm_15m.json").read_text())


def m(spec, fn):
    s = copy.deepcopy(spec)
    fn(s)
    return s


def rename_ids(s):
    s["strategy_id"] = "totally_new_trend_idea"
    s["name"] = "Fresh momentum concept"
    s["version"] = "9.9.9"
    s["hypothesis"]["statement"] = "A completely different narrative about the same rule set, honestly."
    s["experiment"]["trial_family_id"] = "FAM-BRAND-NEW"
    for ind, new in zip(s["signal"]["indicators"], ("alpha", "beta")):
        old = ind["id"]
        ind["id"] = new
        txt = json.dumps(s["signal"]).replace(f'"{old}"', f'"{new}"')
        s["signal"] = json.loads(txt)


def rename_params(s):
    v = s["parameters"]["values"]
    v["short_len"] = v.pop("fast")
    v["long_len"] = v.pop("slow")
    s["parameters"]["constraints"] = [{"left": "short_len", "op": "<", "right": "long_len"}]
    s["signal"]["indicators"][0]["params"]["period"] = {"param": "short_len"}
    s["signal"]["indicators"][1]["params"]["period"] = {"param": "long_len"}


def swap_and_order(s):
    s["signal"]["entry_rules"][0]["when"]["args"].reverse()


def mirror_exit(s):
    # cross_below(fast, slow) == cross_above(slow, fast)
    w = s["signal"]["exit_rules"][0]["when"]
    s["signal"]["exit_rules"][0]["when"] = {"op": "cross_above", "left": w["right"], "right": w["left"]}


def literal_periods(s):
    s["signal"]["indicators"][0]["params"]["period"] = 7
    s["signal"]["indicators"][1]["params"]["period"] = 33


def nest_and(s):
    a = s["signal"]["entry_rules"][0]["when"]["args"]
    s["signal"]["entry_rules"][0]["when"] = {"op": "and", "args": [{"op": "and", "args": [a[0]]}, a[1], a[1]]}


def cosmetic_costs_and_data(s):
    s["costs"]["fee"]["rate_side"] = 0.0005
    s["position_sizing"]["units"] = 2000
    s["data_requirements"]["datasets"][0]["snapshot_id"] = "a newer snapshot"


def test_evasions_collapse_to_same_structure():
    base = structure_hash(SMA)
    for fn in (rename_ids, rename_params, swap_and_order, mirror_exit, literal_periods, nest_and,
               cosmetic_costs_and_data):
        assert structure_hash(m(SMA, fn)) == base, fn.__name__


def test_orb_time_constant_change_same_structure():
    s = m(ORB, lambda s: s["signal"]["entry_rules"][0]["when"]["args"][1].__setitem__("time_local", "11:30"))
    assert structure_hash(s) == structure_hash(ORB)


def test_real_differences_change_structure():
    base = structure_hash(SMA)
    assert structure_hash(m(SMA, lambda s: s["signal"]["indicators"][0].__setitem__("type", "EMA"))) != base
    assert structure_hash(m(SMA, lambda s: s["universe"].__setitem__("timeframe", "30m"))) != base
    assert structure_hash(ORB) != base and structure_hash(ATR) != base


def test_family_index_exact_similar_new(tmp_path):
    idx = FamilyIndex(tmp_path / "families.jsonl")
    a = idx.assign(SMA)
    assert a.mode == "NEW"
    b = idx.assign(m(SMA, rename_ids))
    assert (b.mode, b.family_id) == ("EXACT", a.family_id)
    assert b.declared_family_id == "FAM-BRAND-NEW"          # declared id is recorded but ignored

    # decorate with an extra filter -> similar, merged into the same family
    def add_filter(s):
        s["signal"]["entry_rules"][0]["when"]["args"].append({"op": "time_lt", "time_local": "12:00"})
    c = idx.assign(m(SMA, add_filter))
    assert c.mode == "SIMILAR" and c.family_id == a.family_id and c.similarity >= 0.7

    d = idx.assign(ORB)                                      # unrelated idea -> new family
    assert d.mode == "NEW" and d.family_id != a.family_id
    assert idx.families() == {a.family_id, d.family_id}
    idx.log.verify()


def test_similarity_respects_market_bucket(tmp_path):
    idx = FamilyIndex(tmp_path / "f.jsonl")
    a = idx.assign(SMA)
    other_market = m(SMA, lambda s: s["universe"].__setitem__("market", "IBKR"))
    assert idx.assign(other_market).family_id != a.family_id


def test_canonical_structure_has_no_names():
    txt = json.dumps(canonical_structure(SMA))
    for leak in ("sma_fast", "sma_slow", "golden_cross", "fast", "2330"):
        assert leak not in txt
