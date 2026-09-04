from app.lexicon.store import LexiconRow
from app.lexicon.normalize import normalize_text

G = [LexiconRow(1, "alias", "混凝土", ["砼", "混泥土"]),
     LexiconRow(2, "confusable", "箍筋", ["钢筋"], "子类区分"),
     LexiconRow(3, "alias", "高强螺栓", ["高强度螺栓"])]

def test_variant_to_canonical():
    assert normalize_text("采用砼浇筑，混泥土强度合格", G) == "采用混凝土浇筑，混凝土强度合格"

def test_canonical_untouched_confusable_ignored():
    assert normalize_text("混凝土", G) == "混凝土"
    assert "箍筋" not in "钢筋强度" and normalize_text("钢筋强度 箍筋", G).count("箍筋") == 1

def test_longer_variant_replaced_first():
    assert normalize_text("高强度螺栓连接 混泥土", G) == "高强螺栓连接 混凝土"
