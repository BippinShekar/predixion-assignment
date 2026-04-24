from pydantic import BaseModel, Field
from typing import Literal, Optional


class Source(BaseModel):
    url: str
    title: str
    relevance_score: float = Field(ge=0.0, le=1.0)


class Finding(BaseModel):
    claim: str
    source_urls: list[str]


class JudgeVerdict(BaseModel):
    groundedness_score: float = Field(ge=0.0, le=1.0)
    flagged_claims: list[str]
    passed: bool
    reasoning: str = ""


class ResearchResult(BaseModel):
    question: str
    answer: str
    key_findings: list[Finding]
    sources: list[Source]
    confidence: Literal["high", "medium", "low"]
    limitations: list[str]
    assumptions: list[str]
    next_steps: list[str]
    judge_verdict: Optional[JudgeVerdict] = None
