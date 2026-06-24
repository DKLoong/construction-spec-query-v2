"""写入种子分类规则（六维关键词）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import init_db, get_db, get_connection


# 六维种子规则定义
# 每个规则: (dimension, sub_field, pattern, match_type, priority, threshold)
RULES: list[tuple] = [
    # ===== 维度一：规范属性 (dim1) =====
    # -- 层级 hierarchy --
    ("dim1", "hierarchy", "国家标准", "keyword", 3, 0.3),
    ("dim1", "hierarchy", "行业标准", "keyword", 3, 0.3),
    ("dim1", "hierarchy", "地方标准", "keyword", 3, 0.3),
    ("dim1", "hierarchy", "团体标准", "keyword", 2, 0.3),
    ("dim1", "hierarchy", "企业标准", "keyword", 2, 0.3),
    # -- 性质 nature --
    ("dim1", "nature", "强制性", "keyword", 2, 0.3),
    ("dim1", "nature", "推荐性", "keyword", 2, 0.3),
    # -- 体系层次 sys_level --
    ("dim1", "sys_level", "基础标准", "keyword", 1, 0.3),
    ("dim1", "sys_level", "通用标准", "keyword", 1, 0.3),
    ("dim1", "sys_level", "专用标准", "keyword", 1, 0.3),
    # -- 规范类型 spec_type --
    ("dim1", "spec_type", "项目规范", "keyword", 1, 0.3),
    ("dim1", "spec_type", "通用技术规范", "keyword", 1, 0.3),

    # ===== 维度二：工程阶段 (dim2) =====
    # 前期
    ("dim2", "stage", "勘察", "keyword", 2, 0.5),
    ("dim2", "stage", "测量", "keyword", 2, 0.5),
    ("dim2", "stage", "规划", "keyword", 2, 0.5),
    # 设计
    ("dim2", "stage", "方案设计", "keyword", 2, 0.5),
    ("dim2", "stage", "初步设计", "keyword", 2, 0.5),
    ("dim2", "stage", "施工图设计", "keyword", 2, 0.5),
    ("dim2", "stage", "设计", "keyword", 1, 0.5),
    # 施工
    ("dim2", "stage", "施工准备", "keyword", 2, 0.5),
    ("dim2", "stage", "施工", "keyword", 1, 0.5),
    ("dim2", "stage", "验收", "keyword", 1, 0.5),
    ("dim2", "stage", "竣工", "keyword", 2, 0.5),
    # 运维
    ("dim2", "stage", "试验", "keyword", 1, 0.5),
    ("dim2", "stage", "检测", "keyword", 1, 0.5),
    ("dim2", "stage", "维护", "keyword", 2, 0.5),
    ("dim2", "stage", "改造", "keyword", 2, 0.5),
    ("dim2", "stage", "加固", "keyword", 2, 0.5),

    # ===== 维度三：工程类型 (dim3) =====
    # 按用途 usage
    ("dim3", "usage", "住宅", "keyword", 2, 0.5),
    ("dim3", "usage", "学校", "keyword", 2, 0.5),
    ("dim3", "usage", "医院", "keyword", 2, 0.5),
    ("dim3", "usage", "商场", "keyword", 2, 0.5),
    ("dim3", "usage", "办公楼", "keyword", 2, 0.5),
    ("dim3", "usage", "工业建筑", "keyword", 2, 0.5),
    ("dim3", "usage", "厂房", "keyword", 2, 0.5),
    ("dim3", "usage", "仓库", "keyword", 2, 0.5),
    ("dim3", "usage", "农业建筑", "keyword", 2, 0.5),
    ("dim3", "usage", "温室", "keyword", 2, 0.5),
    # 按建设性质 construction
    ("dim3", "construction", "新建", "keyword", 2, 0.5),
    ("dim3", "construction", "扩建", "keyword", 2, 0.5),
    ("dim3", "construction", "改建", "keyword", 2, 0.5),
    # 按规模 scale
    ("dim3", "scale", "大型", "keyword", 1, 0.5),
    ("dim3", "scale", "中型", "keyword", 1, 0.5),
    ("dim3", "scale", "小型", "keyword", 1, 0.5),

    # ===== 维度四：所属专业 (dim4) =====
    # 建筑工程类
    ("dim4", "specialty", "结构", "keyword", 2, 0.6),
    ("dim4", "specialty", "给排水", "keyword", 2, 0.6),
    ("dim4", "specialty", "暖通", "keyword", 2, 0.6),
    ("dim4", "specialty", "电气", "keyword", 2, 0.6),
    ("dim4", "specialty", "消防", "keyword", 2, 0.6),
    ("dim4", "specialty", "建筑", "keyword", 1, 0.6),
    # 市政公用类
    ("dim4", "specialty", "道路", "keyword", 2, 0.6),
    ("dim4", "specialty", "桥梁", "keyword", 2, 0.6),
    ("dim4", "specialty", "隧道", "keyword", 2, 0.6),
    ("dim4", "specialty", "给水", "keyword", 2, 0.6),
    ("dim4", "specialty", "排水", "keyword", 2, 0.6),
    ("dim4", "specialty", "燃气", "keyword", 2, 0.6),
    ("dim4", "specialty", "热力", "keyword", 2, 0.6),
    ("dim4", "specialty", "轨道交通", "keyword", 2, 0.6),
    ("dim4", "specialty", "环卫", "keyword", 2, 0.6),
    ("dim4", "specialty", "市政", "keyword", 1, 0.6),
    # 铁路工程类
    ("dim4", "specialty", "轨道", "keyword", 2, 0.6),
    ("dim4", "specialty", "路基", "keyword", 2, 0.6),
    ("dim4", "specialty", "接触网", "keyword", 2, 0.6),
    ("dim4", "specialty", "信号", "keyword", 2, 0.6),
    ("dim4", "specialty", "铁路", "keyword", 1, 0.6),

    # ===== 维度五：工程部位 (dim5) =====
    # 地基与基础
    ("dim5", "location", "桩基", "keyword", 2, 0.7),
    ("dim5", "location", "承台", "keyword", 2, 0.7),
    ("dim5", "location", "地下室", "keyword", 2, 0.7),
    ("dim5", "location", "基坑", "keyword", 2, 0.7),
    ("dim5", "location", "地基", "keyword", 1, 0.7),
    ("dim5", "location", "基础", "keyword", 1, 0.7),
    # 主体结构
    ("dim5", "location", "梁", "keyword", 1, 0.7),
    ("dim5", "location", "板", "keyword", 1, 0.7),
    ("dim5", "location", "柱", "keyword", 1, 0.7),
    ("dim5", "location", "剪力墙", "keyword", 2, 0.7),
    ("dim5", "location", "钢结构", "keyword", 2, 0.7),
    ("dim5", "location", "主体结构", "keyword", 2, 0.7),
    # 屋面
    ("dim5", "location", "防水层", "keyword", 2, 0.7),
    ("dim5", "location", "保温层", "keyword", 2, 0.7),
    ("dim5", "location", "屋面", "keyword", 1, 0.7),
    # 装饰装修
    ("dim5", "location", "装饰", "keyword", 1, 0.7),
    ("dim5", "location", "装修", "keyword", 1, 0.7),
    ("dim5", "location", "抹灰", "keyword", 2, 0.7),
    ("dim5", "location", "涂饰", "keyword", 2, 0.7),
    ("dim5", "location", "幕墙", "keyword", 2, 0.7),
    # 机电
    ("dim5", "location", "机电", "keyword", 1, 0.7),
    ("dim5", "location", "弱电", "keyword", 2, 0.7),
    ("dim5", "location", "通风", "keyword", 2, 0.7),

    # ===== 维度六：材料/工艺 (dim6) =====
    # 混凝土
    ("dim6", "material", "混凝土", "keyword", 1, 0.6),
    ("dim6", "material", "现浇", "keyword", 2, 0.6),
    ("dim6", "material", "预制", "keyword", 2, 0.6),
    ("dim6", "material", "预应力", "keyword", 2, 0.6),
    ("dim6", "material", "水泥", "keyword", 1, 0.6),
    # 金属
    ("dim6", "material", "钢筋", "keyword", 2, 0.6),
    ("dim6", "material", "钢管", "keyword", 2, 0.6),
    ("dim6", "material", "型钢", "keyword", 2, 0.6),
    ("dim6", "material", "螺栓", "keyword", 2, 0.6),
    ("dim6", "material", "钢材", "keyword", 1, 0.6),
    ("dim6", "material", "焊接", "keyword", 1, 0.6),
    # 砌体
    ("dim6", "material", "砖", "keyword", 1, 0.6),
    ("dim6", "material", "砌块", "keyword", 2, 0.6),
    ("dim6", "material", "砌体", "keyword", 1, 0.6),
    ("dim6", "material", "石材", "keyword", 2, 0.6),
    # 防水
    ("dim6", "material", "卷材", "keyword", 2, 0.6),
    ("dim6", "material", "防水", "keyword", 1, 0.6),
    # 模板
    ("dim6", "material", "模板", "keyword", 1, 0.6),
    ("dim6", "material", "爬模", "keyword", 2, 0.6),
    ("dim6", "material", "脚手架", "keyword", 2, 0.6),
    # 装饰材料
    ("dim6", "material", "涂料", "keyword", 2, 0.6),
    ("dim6", "material", "玻璃", "keyword", 1, 0.6),
    ("dim6", "material", "保温", "keyword", 1, 0.6),
    ("dim6", "material", "隔热", "keyword", 1, 0.6),
]


def seed(conn=None):
    """写入种子规则。如提供 conn 则复用，否则创建新连接。"""
    own_conn = conn is None
    if own_conn:
        init_db()
        c = get_connection()
    else:
        c = conn

    try:
        inserted = 0
        for dim, sub_field, pattern, match_type, priority, threshold in RULES:
            # 幂等：检查是否已存在相同规则
            existing = c.execute(
                """SELECT id FROM classification_rules
                   WHERE dimension = ? AND sub_field = ? AND pattern = ?""",
                (dim, sub_field, pattern),
            ).fetchone()
            if existing:
                continue

            c.execute(
                """INSERT INTO classification_rules
                   (dimension, sub_field, pattern, match_type, priority, threshold, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, 1)""",
                (dim, sub_field, pattern, match_type, priority, threshold),
            )
            inserted += 1

        if own_conn:
            c.commit()

        print(f"种子规则写入完成：{inserted} 条新增（共 {len(RULES)} 条定义）")
    finally:
        if own_conn:
            c.close()


if __name__ == "__main__":
    seed()
