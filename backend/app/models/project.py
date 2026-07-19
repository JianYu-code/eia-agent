import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, JSON
from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class Project(Base):
    __tablename__ = "projects"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(500), default="")
    filename = Column(String(500), default="")
    file_path = Column(String(1000), default="")
    file_size = Column(Integer, default=0)
    audit_domain = Column(String(50), default="eia")
    status = Column(String(20), default="uploaded")
    progress = Column(Float, default=0.0)
    step = Column(String(200), default="等待审核任务")
    issues = Column(JSON, default=dict)
    logs = Column(JSON, default=list)
    report_path = Column(String(1000), default="")
    report_formats = Column(JSON, default=list)
    result_summary = Column(JSON, default=dict)
    deleted_by_user = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        issues = self.issues or {}
        p0_val = issues.get("P0", 0)
        p1_val = issues.get("P1", 0)
        p2_val = issues.get("P2", 0)
        p0 = len(p0_val) if isinstance(p0_val, list) else (p0_val or 0)
        p1 = len(p1_val) if isinstance(p1_val, list) else (p1_val or 0)
        p2 = len(p2_val) if isinstance(p2_val, list) else (p2_val or 0)
        return {
            "id": self.id,
            "name": self.name,
            "filename": self.filename,
            "audit_domain": self.audit_domain,
            "status": self.status,
            "progress": self.progress,
            "step": self.step,
            "issues": {"P0": p0, "P1": p1, "P2": p2},
            "result_summary": self.result_summary or {},
            "report_url": f"/api/projects/{self.id}/report/view" if self.report_path else None,
            "logs": self.logs,
            "deleted_by_user": self.deleted_by_user,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class LLMProfile(Base):
    __tablename__ = "llm_profiles"

    id = Column(String(100), primary_key=True)
    name = Column(String(200), default="")
    base_url = Column(String(500), default="")
    model = Column(String(200), default="")
    api_key = Column(String(500), default="")
    purpose = Column(String(50), default="audit")
    pool_enabled = Column(Boolean, default=True)
    max_retries = Column(Integer, default=3)
    extra_body = Column(JSON, default=None)
    active = Column(Boolean, default=False)
    vision_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "api_key_configured": bool(self.api_key),
            "api_key_masked": "****" + self.api_key[-4:] if self.api_key else "未配置",
            "purpose": self.purpose,
            "pool_enabled": self.pool_enabled,
            "max_retries": self.max_retries,
            "extra_body": self.extra_body,
            "active": self.active,
            "vision_active": self.vision_active,
            "category": self.purpose,
        }


class FileIndex(Base):
    __tablename__ = "file_index"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    title = Column(String(500), default="")
    original_name = Column(String(500), default="")
    file_path = Column(String(1000), default="")
    file_type = Column(String(20), default="")
    file_size = Column(Integer, default=0)
    category = Column(String(500), default="")
    standard_id = Column(String(200), default="")
    deprecated = Column(Boolean, default=False)
    replaced_by = Column(String(500), default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "original_name": self.original_name,
            "file_type": self.file_type,
            "file_size": self.file_size,
            "category": self.category,
            "standard_id": self.standard_id,
            "deprecated": self.deprecated,
            "replaced_by": self.replaced_by or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class KnowledgeFile(Base):
    """K-Hub 知识文件：投递入库追踪（SHA256 判重）"""
    __tablename__ = "knowledge_files"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    sha256 = Column(String(64), unique=True, index=True)
    name = Column(String(500), default="")
    source_path = Column(String(1000), default="")
    vault_path = Column(String(1000), default="")
    category = Column(String(100), default="其他")
    size = Column(Integer, default=0)
    summary = Column(String(2000), default="")
    channel = Column(String(20), default="extractor")
    status = Column(String(20), default="active")
    error = Column(String(1000), default="")
    imported_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "sha256": self.sha256,
            "name": self.name,
            "source_path": self.source_path,
            "vault_path": self.vault_path,
            "category": self.category,
            "size": self.size,
            "summary": self.summary,
            "channel": self.channel,
            "status": self.status,
            "error": self.error,
            "imported_at": self.imported_at.isoformat() if self.imported_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class KVSetting(Base):
    """键值配置（知识库路径、整理进度等）"""
    __tablename__ = "kv_settings"

    key = Column(String(100), primary_key=True)
    value = Column(JSON, default=None)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditCase(Base):
    """历史审核案例库 — feedback=accurate 问题聚类凝练，作为 few-shot 注入审核 prompt"""
    __tablename__ = "audit_cases"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    rule_id = Column(String(50), default="", index=True)
    category = Column(String(100), default="")
    title_pattern = Column(String(500), default="")
    typical_finding = Column(String(2000), default="")
    key_points = Column(String(2000), default="")
    accurate_count = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "category": self.category,
            "title_pattern": self.title_pattern,
            "typical_finding": self.typical_finding,
            "key_points": self.key_points,
            "accurate_count": self.accurate_count,
            "enabled": self.enabled,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AuditIssue(Base):
    """审核问题结构化落库 — 支撑反馈闭环与质量统计"""
    __tablename__ = "audit_issues"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    project_id = Column(String(36), index=True)
    rule_id = Column(String(50), default="", index=True)
    severity = Column(String(5), default="P2")
    category = Column(String(100), default="")
    title = Column(String(500), default="")
    finding = Column(String(4000), default="")
    evidence = Column(String(2000), default="")
    evidence_location = Column(String(1000), default="")
    reasoning = Column(String(4000), default="")
    law_ref = Column(String(500), default="")
    suggestion = Column(String(2000), default="")
    step = Column(String(100), default="")
    chapter = Column(String(200), default="")
    feedback = Column(String(20), default="")
    feedback_note = Column(String(2000), default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "category": self.category,
            "title": self.title,
            "finding": self.finding,
            "evidence": self.evidence,
            "evidence_location": self.evidence_location,
            "reasoning": self.reasoning,
            "law_ref": self.law_ref,
            "suggestion": self.suggestion,
            "step": self.step,
            "chapter": self.chapter,
            "feedback": self.feedback,
            "feedback_note": self.feedback_note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
