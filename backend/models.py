# ─── Salesforce (AI)nspector — models.py ─────────────────────────────────────

from typing import Optional, Literal
from pydantic import BaseModel, Field


class HistoryMessage(BaseModel):
    role: str                   # 'user' or 'assistant'
    content: str


class PageContext(BaseModel):
    url: Optional[str] = None
    title: Optional[str] = None
    breadcrumb: Optional[str] = None
    heading: Optional[str] = None
    error_messages: Optional[list[str]] = []


class ChatRequest(BaseModel):
    message: str                = Field(..., description='User message')
    session_id: str             = Field(..., description='Salesforce session token')
    instance_url: str           = Field(..., description='Salesforce org URL')
    api_key: str                = Field(..., description='LLM provider API key')
    llm_provider: Literal[
        'anthropic', 'openai', 'google'
    ]                           = Field(default='anthropic', description='LLM provider')
    history: list[HistoryMessage] = Field(default=[], description='Conversation history')
    page_context: Optional[PageContext] = None
    is_production: bool         = Field(default=False, description='Client-side production hint')
    treat_as_sandbox: bool      = Field(default=False, description='Client-side sandbox override')

    # Optional file attachment
    file_name: Optional[str]    = None
    file_content: Optional[str] = None
    file_type: Optional[str]    = None


class HealthResponse(BaseModel):
    status: str
    version: str