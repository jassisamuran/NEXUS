# app/tasks/models.py
import enum
import uuid

from sqlalchemy import Column, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.database.connection import Base


class TaskStatus(str, enum.Enum):
    QUEUED = "queued"
    CLONING = "cloning"
    INDEXING = "indexing"
    PLANNING = "planning"
    CODING = "coding"
    TESTING = "testing"
    REVIEWING = "reviewing"
    CREATING_PR = "creating_pr"
    COMPLETE = "complete"
    FAILED = "failed"


class Task(Base):
    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )

    # Input
    repo_url = Column(String(500), nullable=False)
    task_description = Column(Text, nullable=False)
    branch_name = Column(String(200))

    # Status
    status = Column(Enum(TaskStatus), default=TaskStatus.QUEUED, index=True)
    progress_percent = Column(Integer, default=0)
    current_stage = Column(String(100))

    # Output
    pr_url = Column(String(500))
    pr_number = Column(Integer)
    files_changed = Column(JSONB, default=list)
    error_message = Column(Text)

    # Cost tracking
    tokens_used = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    duration_seconds = Column(Integer)

    # Celery
    celery_task_id = Column(String(200))

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))


class TaskLog(Base):
    __tablename__ = "task_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(
        UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False, index=True
    )

    agent_name = Column(String(100))
    event_type = Column(String(100))
    content = Column(Text)
    metadata_ = Column(JSONB, default=dict)
    tokens = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
