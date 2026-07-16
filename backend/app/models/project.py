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
