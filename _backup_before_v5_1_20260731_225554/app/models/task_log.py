import enum
from sqlalchemy import Column, Enum, Integer, String, Text
from app.core.database import Base
from app.models.mixins import TimestampMixin


class TaskStatus(str, enum.Enum):
    PENDING = 'pending'
    RUNNING = 'running'
    SUCCESS = 'success'
    FAILED = 'failed'


class TaskLog(Base, TimestampMixin):
    __tablename__ = 'task_logs'

    id = Column(Integer, primary_key=True, index=True)
    task_name = Column(String(140), nullable=False)
    task_id = Column(String(140), default='')
    status = Column(Enum(TaskStatus), default=TaskStatus.PENDING, nullable=False)
    message = Column(Text, default='')
    payload_json = Column(Text, default='{}')
