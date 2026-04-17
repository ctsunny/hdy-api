from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Database row representations (returned from queries)
# ---------------------------------------------------------------------------

class Product(BaseModel):
    id: Optional[int] = None
    pid: int
    name: Optional[str] = None
    price: Optional[str] = None
    stock_status: Optional[str] = None   # "in_stock" | "out_of_stock" | "unknown"
    raw_data: Optional[str] = None        # JSON string
    first_seen_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    last_changed_at: Optional[datetime] = None


class ChangeLog(BaseModel):
    id: Optional[int] = None
    pid: int
    field_name: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    changed_at: Optional[datetime] = None


class NotifyLog(BaseModel):
    id: Optional[int] = None
    pid: Optional[int] = None
    channel: str
    message: str
    sent_at: Optional[datetime] = None
    success: bool = False


# ---------------------------------------------------------------------------
# Config stored in the database (single row)
# ---------------------------------------------------------------------------

class CrawlerConfig(BaseModel):
    start_pid: int = 1150
    end_pid: int = 1200
    interval_ms: int = 1500
    loop_enabled: bool = False
    login_cookie: Optional[str] = None
    notify_channels: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# API request / response schemas
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class SiteLoginRequest(BaseModel):
    """Credentials for szhdy.com (username + API key to obtain a JWT token)."""
    username: str
    api_key: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ConfigUpdate(BaseModel):
    start_pid: Optional[int] = None
    end_pid: Optional[int] = None
    exec_start_pid: Optional[int] = None
    interval_ms: Optional[int] = None
    loop_enabled: Optional[bool] = None
    login_cookie: Optional[str] = None
    notify_channels: Optional[dict[str, Any]] = None
    notify_price_min: Optional[float] = None
    notify_price_max: Optional[float] = None
    notify_monthly_price_min: Optional[float] = None
    notify_monthly_price_max: Optional[float] = None


class CrawlerStatus(BaseModel):
    running: bool
    current_pid: Optional[int] = None
    start_pid: Optional[int] = None
    end_pid: Optional[int] = None
    exec_start_pid: Optional[int] = None
    loop_enabled: bool = False
    checked_count: int = 0
    changed_count: int = 0
    notify_paused: bool = False
    filter_sold_out: bool = False


class NotifyTestRequest(BaseModel):
    channel: str
    config: dict[str, Any]


class PaginatedChanges(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ChangeLog]


class SiteAccount(BaseModel):
    id: int
    label: Optional[str] = None
    username: str
    is_active: bool = False
    created_at: Optional[str] = None


class SiteAccountCreate(BaseModel):
    label: Optional[str] = None
    username: str
    api_key: str


class PaginatedProducts(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[dict]


class VisitorUser(BaseModel):
    id: int
    username: str
    label: Optional[str] = None
    notify_channels: dict[str, Any] = Field(default_factory=dict)
    notify_price_min: Optional[float] = None
    notify_price_max: Optional[float] = None
    notify_monthly_price_min: Optional[float] = None
    notify_monthly_price_max: Optional[float] = None
    created_at: Optional[str] = None
    is_active: bool = True


class VisitorUserCreate(BaseModel):
    username: str
    password: str
    label: Optional[str] = None


class VisitorLoginRequest(BaseModel):
    username: str
    password: str


class VisitorNotifyUpdate(BaseModel):
    notify_channels: dict[str, Any]
    notify_price_min: Optional[float] = None
    notify_price_max: Optional[float] = None
    notify_monthly_price_min: Optional[float] = None
    notify_monthly_price_max: Optional[float] = None


class VisitorPasswordUpdate(BaseModel):
    password: str


# ---------------------------------------------------------------------------
# Agent client models
# ---------------------------------------------------------------------------

class AgentCreate(BaseModel):
    name: str
    server_url: Optional[str] = None


class AgentTaskAssign(BaseModel):
    start_pid: int = 1150
    end_pid: int = 1200
    interval_ms: int = 1500
    loop_enabled: bool = False
    # Optional custom PID list (newline or comma-separated integers).
    # When provided, the agent scans only these PIDs instead of the start–end range.
    pid_list: Optional[str] = None
    # Optional site account ID to use for authentication.
    # When provided, the token of this account is embedded in the task.
    site_account_id: Optional[int] = None


class AgentNotifyUpdate(BaseModel):
    notify_channels: dict[str, Any] = Field(default_factory=dict)


class AgentHeartbeatRequest(BaseModel):
    version: Optional[str] = None
    status: Optional[str] = None


class AgentReportRequest(BaseModel):
    pid: int
    name: Optional[str] = None
    price: Optional[str] = None
    stock_status: Optional[str] = None
    changed_fields: list[str] = Field(default_factory=list)
