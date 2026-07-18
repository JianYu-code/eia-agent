# Dify Code 节点 — 报告文本预处理

import re

def main(report_text: str) -> dict:
    report_type = "报告表" if "报告表" in report_text[:2000] else "报告书"
    chapters = re.findall(r"^第[一二三四五六七八九十\d]+章\s*(.*)", report_text, re.MULTILINE)
    stds = re.findall(r"(?:GB|GB/T|HJ|HJ/T)\s*[\d.\-—]+(?:\s*[—\-]\s*\d{4})?", report_text)[:15]
    has_boiler = any(kw in report_text[:3000] for kw in ["锅炉","燃烧","煤","生物质","天然气"])
    has_hw = any(kw in report_text for kw in ["危废","危险废物","HW"])
    has_vocs = any(kw in report_text[:3000] for kw in ["VOCs","挥发性","涂装","喷漆","印刷","有机废气"])
    
    return {
        "report_type": report_type,
        "chapters": chapters[:20],
        "standards": list(set(stds)),
        "has_boiler": has_boiler,
        "has_hw": has_hw,
        "has_vocs": has_vocs,
        "text_preview": report_text[:500],
    }
