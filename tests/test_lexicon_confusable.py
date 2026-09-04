from app.lexicon.store import LexiconRow
from app.lexicon.confusable import detect_confusable

P = [LexiconRow(1, "confusable", "箍筋", ["钢筋"], "箍筋是钢筋加工成型的构造钢筋（子类）"),
     LexiconRow(2, "confusable", "静力触探", ["动力触探"], "两种地基勘探方法，加载方式不同")]

def test_both_hit():
    hits = detect_confusable("静力触探和动力触探有何区别", P)
    assert len(hits) == 1 and hits[0]["a"] == "静力触探" and hits[0]["b"] == "动力触探"
    assert "加载" in hits[0]["distinguish"]

def test_single_no_hit_and_empty():
    assert detect_confusable("静力触探适用什么土层", P) == []
    assert detect_confusable("", P) == []
