from app.engine.extractor import package_completeness
from app.engine.grader import build_issue

REQUIRED_P0 = ["应急预案", "风险评估", "资源调查"]
REQUIRED_P1 = ["编制说明", "发布令", "评审意见"]


async def check_emergency_package(text_data: dict, audit_ctx: dict | None = None) -> list[dict]:
    """应急预案备案资料包完整性核查（K17 七件套）"""
    issues = []
    files = text_data.get("files") or []

    if not files:
        issues.append(build_issue(
            "R-EMG-PKG-001", "P2", "资料完整性",
            "单文件预案审核，无法核查备案资料包完整性",
            "备案资料包建议包含：应急预案、风险评估、资源调查、编制说明、发布令、评审意见、备案表。",
            law_ref="《企业事业单位突发环境事件应急预案备案管理办法（试行）》（环发〔2015〕4号）",
            suggestion="建议上传完整备案资料包以核查完整性。"
        ))
        return issues

    for m in package_completeness(files, REQUIRED_P0):
        issues.append(build_issue(
            "R-EMG-PKG-001", "P0", "资料完整性",
            f"备案资料包缺失「{m}」",
            f"资料包内未识别到{m}类文件。",
            law_ref="环发〔2015〕4号",
            suggestion=f"请补充{m}文件。"
        ))
    for m in package_completeness(files, REQUIRED_P1):
        issues.append(build_issue(
            "R-EMG-PKG-002", "P1", "资料完整性",
            f"备案资料包缺失「{m}」",
            f"资料包内未识别到{m}类文件。",
            law_ref="环发〔2015〕4号",
            suggestion=f"建议补充{m}文件。"
        ))
    return issues
