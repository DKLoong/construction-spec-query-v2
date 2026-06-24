from app.models import SpecCreate, ClauseCreate, ClassificationRuleCreate


def test_spec_create_validation():
    spec = SpecCreate(code="GB 50204-2015", title="混凝土结构工程施工质量验收规范")
    assert spec.code == "GB 50204-2015"


def test_spec_create_optional_fields():
    spec = SpecCreate(
        code="GB 50204-2015",
        title="混凝土结构工程施工质量验收规范",
        dim1_hierarchy="国家标准",
        dim1_nature="强制性",
        dim2_stage="施工阶段",
    )
    assert spec.dim1_hierarchy == "国家标准"


def test_clause_create_required():
    clause = ClauseCreate(spec_id=1, clause_no="5.2.1", content="模板及其支架应...")
    assert clause.clause_no == "5.2.1"


def test_clause_create_with_dimensions():
    clause = ClauseCreate(
        spec_id=1,
        clause_no="5.2.1",
        content="模板及其支架应根据工程结构形式...",
        dim6_material="混凝土材料,模板工程",
    )
    assert "模板工程" in clause.dim6_material


def test_rule_create():
    rule = ClassificationRuleCreate(
        dimension="dim6",
        sub_field="material",
        pattern="混凝土",
        threshold=0.6,
    )
    assert rule.dimension == "dim6"
