from datetime import datetime
from enum import StrEnum
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy entities."""


class TaskStatus(StrEnum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    FOUND = "FOUND"
    ERROR = "ERROR"


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    domain: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(255))
    encrypted_password: Mapped[str] = mapped_column(Text)
    project_id: Mapped[str] = mapped_column(String(255))
    project_name: Mapped[str] = mapped_column(String(255), default="")
    region: Mapped[str] = mapped_column(String(40), default="ru-3")
    auth_url: Mapped[str] = mapped_column(String(500), default="")
    network_api_url: Mapped[str] = mapped_column(String(500), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_start: Mapped[bool] = mapped_column(Boolean, default=False)
    encrypted_proxy_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    min_interval: Mapped[int] = mapped_column(Integer, default=5)
    max_interval: Mapped[int] = mapped_column(Integer, default=10)
    topic_chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    topic_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notify_account_added: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_errors: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_found: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_no_free_ip: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_permission: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_network: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_rate_limit: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_server: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_unknown: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    subnets = relationship("AccountSubnet", cascade="all, delete-orphan")


class AccountSubnet(Base):
    __tablename__ = "account_subnets"
    __table_args__ = (UniqueConstraint("account_id", "subnet_id", name="uq_account_subnet"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    cidr: Mapped[str] = mapped_column(String(50))
    subnet_id: Mapped[str] = mapped_column(String(36))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class HunterTask(Base):
    __tablename__ = "hunter_tasks"
    __table_args__ = (Index("ix_running_pair", "account_id", "subnet_id", "status"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    subnet_id: Mapped[str] = mapped_column(String(36))
    subnet_cidr: Mapped[str] = mapped_column(String(50))
    network_id: Mapped[str] = mapped_column(String(36))
    region: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default=TaskStatus.RUNNING.value, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    min_interval: Mapped[int] = mapped_column(Integer, default=5)
    max_interval: Mapped[int] = mapped_column(Integer, default=10)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(String(100))
    floating_ip_id: Mapped[str | None] = mapped_column(String(100))
    floating_ip_address: Mapped[str | None] = mapped_column(String(64))
    elapsed_seconds: Mapped[float | None] = mapped_column(Float)


class FoundIP(Base):
    __tablename__ = "found_ips"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    hunter_task_id: Mapped[int] = mapped_column(ForeignKey("hunter_tasks.id", ondelete="CASCADE"))
    subnet_id: Mapped[str] = mapped_column(String(36))
    subnet_cidr: Mapped[str] = mapped_column(String(50))
    floating_ip_id: Mapped[str] = mapped_column(String(100))
    floating_ip_address: Mapped[str] = mapped_column(String(64))
    found_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer)
