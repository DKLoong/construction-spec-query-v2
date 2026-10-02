"""台账不得退回进程内存：源码里不应再出现字典式用法

这是**删除型不变量**的守卫：跑测试证明不了"将来不会被加回来"，所以直接钉住符号不存在。
断言用代码形状（`progress_store.get(` 等）而不是裸词——裸词会命中正常叙述里的历史说明。
"""
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "app" / "routes" / "import_routes.py"


def test_no_inmemory_progress_store_usage():
    text = SRC.read_text(encoding="utf-8")

    assert "progress_store =" not in text, "台账又变回内存字典了"
    assert "progress_store.get(" not in text, "仍在直接读内存字典"
    assert "progress_store.pop(" not in text, "仍在直接写内存字典"
    assert "progress_store[" not in text, "仍在直接下标内存字典"
