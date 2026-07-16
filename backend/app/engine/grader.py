def grade_issues(issues: list[dict]) -> list[dict]:
    graded = {"P0": [], "P1": [], "P2": []}
    for issue in issues:
        severity = issue.get("severity", "P2")
        if severity not in graded:
            severity = "P2"
        graded[severity].append(issue)
    for key in graded:
        graded[key].sort(key=lambda x: x.get("rule_id", ""))
    return graded


def build_issue(rule_id: str, severity: str, category: str, title: str, finding: str, evidence: str = "", law_ref: str = "", suggestion: str = "") -> dict:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "category": category,
        "title": title,
        "finding": finding,
        "evidence": evidence,
        "law_ref": law_ref,
        "suggestion": suggestion,
    }
