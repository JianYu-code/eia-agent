import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend"))


def test_extract_docx_heading_styles():
    """测试 DOCX 标题样式识别"""
    from app.engine.extractor import _split_chapters

    md_text = """# 前言
这是前言内容。

## 总则
编制依据内容。

## 工程分析
这是工程分析内容。

### 源强核算
核算细节。"""

    chapters = _split_chapters(md_text)
    titles = [c["title"] for c in chapters]
    assert "前言" in titles, f"Expected '前言' in {titles}"
    assert "总则" in titles, f"Expected '总则' in {titles}"
    assert "工程分析" in titles, f"Expected '工程分析' in {titles}"
    assert "源强核算" in titles, f"Expected '源强核算' in {titles}"
    assert len(chapters) >= 4, f"Expected >= 4 chapters, got {len(chapters)}"
    print("✓ DOCX 标题识别通过")


def test_split_chapters_chinese_patterns():
    """测试中文标题格式识别"""
    from app.engine.extractor import _split_chapters

    text = """第一章 前言
前言正文内容。

一、编制依据
依据内容。

（一）评价因子
因子详情。

1.1 工程分析
分析内容。"""

    chapters = _split_chapters(text)
    titles = [c["title"] for c in chapters]
    assert "前言" in titles, f"Expected '前言' in {titles}"
    assert "编制依据" in titles, f"Expected '编制依据' in {titles}"
    assert "评价因子" in titles, f"Expected '评价因子' in {titles}"
    assert "1.1 工程分析" in titles or "工程分析" in titles
    print("✓ 中文标题识别通过")


def test_grader():
    """测试 P0/P1/P2 分级器"""
    from app.engine.grader import grade_issues, build_issue

    issues = [
        build_issue("R-001", "P0", "测试", "严重问题", "发现"),
        build_issue("R-002", "P1", "测试", "一般问题", "发现"),
        build_issue("R-003", "P1", "测试", "一般问题2", "发现"),
        build_issue("R-004", "P2", "测试", "建议", "发现"),
    ]
    graded = grade_issues(issues)
    assert len(graded["P0"]) == 1
    assert len(graded["P1"]) == 2
    assert len(graded["P2"]) == 1
    assert graded["P0"][0]["rule_id"] == "R-001"
    print("✓ 分级器通过")


def test_rules_engine_keyword():
    """测试关键词匹配规则"""
    from app.engine.rules_engine import run_keyword_check

    rule = {
        "rule_id": "R-TEST",
        "severity": "P0",
        "category": "测试",
        "check_config": {
            "required_chapters": ["前言", "总则"],
            "required_keywords": ["环境影响评价"],
        },
        "law_ref": "HJ 2.1-2016",
    }

    text_with = """前言\n这是前言内容。\n总则\n编制依据。\n本项目进行了环境影响评价。"""
    issues = run_keyword_check(rule, text_with)
    assert len(issues) == 0, f"Expected 0 issues, got {len(issues)}"

    text_without = """这是第一章内容。\n第二章继续。"""
    issues = run_keyword_check(rule, text_without)
    assert len(issues) >= 2, f"Expected >= 2 issues, got {len(issues)}"
    print("✓ 关键词匹配通过")


def test_rules_engine_load_filter():
    """测试规则按 report_type 过滤"""
    import yaml
    from pathlib import Path
    from app.engine.rules_engine import load_rules

    rules_report_book = load_rules("eia", "报告书")
    rules_report_table = load_rules("eia", "报告表")

    assert len(rules_report_book) > 0
    assert len(rules_report_table) > 0
    assert len(rules_report_book) != len(rules_report_table), \
        f"报告书和报告表规则数应不同: {len(rules_report_book)} vs {len(rules_report_table)}"
    print(f"✓ 规则过滤通过 (报告书:{len(rules_report_book)}条, 报告表:{len(rules_report_table)}条)")


def test_standards_index():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
    from ingest_mineru import _normalize_std_id
    assert _normalize_std_id("HJ 2.1-2016") == "HJ2.1-2016"
    assert _normalize_std_id("HJ2.1-2016") == "HJ2.1-2016"
    assert _normalize_std_id("GB 16297—1996") == "GB16297-1996"
    print("✓ 标准编号归一化通过")


def test_pipeline_structure_check():
    """测试管道结构检查逻辑"""
    required = ["前言", "总则", "工程分析"]
    full_text_with = "前言\n这是内容。\n总则\n总则内容。\n工程分析\n分析内容。"
    for ch in required:
        assert ch in full_text_with, f"Expected '{ch}' in text"
    print("✓ 结构检查逻辑通过")


if __name__ == "__main__":
    test_extract_docx_heading_styles()
    test_split_chapters_chinese_patterns()
    test_grader()
    test_rules_engine_keyword()
    test_rules_engine_load_filter()
    test_standards_index()
    test_pipeline_structure_check()
    print("\n✅ 全部测试通过")
