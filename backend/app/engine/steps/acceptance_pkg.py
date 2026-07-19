from app.engine.extractor import package_completeness
from app.engine.grader import build_issue

REQUIRED_P0 = ["验收监测报告", "验收意见", "环评批复"]
REQUIRED_P1 = ["其他说明事项", "监测附件", "信息公开证据"]


async def check_acceptance_package(text_data: dict, audit_ctx: dict | None = None) -> list[dict]:
    """验收资料包完整性核查（K16 六件套）"""
    issues = []
    files = text_data.get("files") or []

    if not files:
        issues.append(build_issue(
            "R-ACC-PKG-001", "P1", "资料完整性",
            "单文件验收报告，无法核查资料包完整性",
            "本次仅上传单个文件。完整验收资料包应包含：验收监测报告、验收意见、环评批复、其他说明事项、监测附件、信息公开证据。",
            law_ref="《建设项目竣工环境保护验收暂行办法》（国环规环评〔2017〕4号）",
            suggestion="建议上传完整验收资料包/文件夹，以核查批复、附件与公开证据完整性。"
        ))
        return issues

    missing_p0 = package_completeness(files, REQUIRED_P0)
    missing_p1 = package_completeness(files, REQUIRED_P1)

    for m in missing_p0:
        issues.append(build_issue(
            "R-ACC-PKG-001", "P0", "资料完整性",
            f"验收资料包缺失「{m}」",
            f"资料包内未识别到{m}类文件（按文件名分类）。",
            law_ref="《建设项目竣工环境保护验收暂行办法》",
            suggestion=f"请补充{m}后重新审核；如文件名不含关键词，请规范命名。"
        ))
    for m in missing_p1:
        issues.append(build_issue(
            "R-ACC-PKG-002", "P1", "资料完整性",
            f"验收资料包缺失「{m}」",
            f"资料包内未识别到{m}类文件。",
            law_ref="《建设项目竣工环境保护验收暂行办法》",
            suggestion=f"建议补充{m}。"
        ))
    return issues
