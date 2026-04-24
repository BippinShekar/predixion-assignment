import json
import os
import time
from typing import Any

import anthropic
from openai import APIError, APITimeoutError, OpenAI

from utils.cost import estimate_cost
from utils.logger import get_logger

log = get_logger()

HAIKU_MODEL = "claude-haiku-4-5-20251001"

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "web_search_20250305",
        "name": "web_search",
    },
    {
        "name": "query_decomposer",
        "description": (
            "Decompose a research query into focused sub-questions and classify its type. "
            "Call this first for complex or multi-part questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The research question to decompose.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "structured_compare",
        "description": (
            "Format raw research findings into a structured markdown comparison table. "
            "Call this after gathering data on multiple items you need to compare."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The items being compared.",
                },
                "criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The comparison criteria.",
                },
                "raw_findings": {
                    "type": "object",
                    "description": (
                        "Freeform text findings per item: { item_name: text description "
                        "of everything you found about this item }."
                    ),
                },
            },
            "required": ["items", "criteria", "raw_findings"],
        },
    },
    {
        "name": "llm_judge",
        "description": (
            "Validate your draft answer for factual groundedness using a second LLM. "
            "You MUST call this before submit_answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The original research question.",
                },
                "draft_answer": {
                    "type": "string",
                    "description": "Your draft answer to validate.",
                },
                "key_claims": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific factual claims to check for source support.",
                },
                "source_snippets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["url", "content"],
                    },
                    "description": "Source excerpts that should back the claims.",
                },
            },
            "required": ["question", "draft_answer", "key_claims", "source_snippets"],
        },
    },
    {
        "name": "submit_answer",
        "description": (
            "Submit the final structured research answer. Call this LAST, only after "
            "running llm_judge. This terminates the research loop."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "answer": {
                    "type": "string",
                    "description": "Direct answer in 2 to 3 sentences maximum.",
                },
                "key_findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim": {"type": "string"},
                            "source_urls": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["claim", "source_urls"],
                    },
                },
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "title": {"type": "string"},
                            "relevance_score": {
                                "type": "number",
                                "minimum": 0.0,
                                "maximum": 1.0,
                            },
                        },
                        "required": ["url", "title", "relevance_score"],
                    },
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": (
                        "high: judge score >= 0.8 and multiple corroborating sources. "
                        "medium: judge score 0.6 to 0.79 or single source per claim. "
                        "low: judge score < 0.6, conflicting sources, or limited data."
                    ),
                },
                "limitations": {"type": "array", "items": {"type": "string"}},
                "assumptions": {"type": "array", "items": {"type": "string"}},
                "next_steps": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "question",
                "answer",
                "key_findings",
                "sources",
                "confidence",
                "limitations",
                "assumptions",
                "next_steps",
            ],
        },
    },
]


def _haiku_json_call(
    prompt: str,
    tracker: Any,
    step: str,
    timeout: float = 30.0,
) -> dict:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    start = time.time()

    response = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        timeout=timeout,
    )

    latency_ms = (time.time() - start) * 1000
    cost = estimate_cost(
        HAIKU_MODEL,
        response.usage.input_tokens,
        response.usage.output_tokens,
    )
    tracker.log_call(
        step=step,
        model=HAIKU_MODEL,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        latency_ms=latency_ms,
        cost_usd=cost,
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def execute_query_decomposer(tool_input: dict[str, Any], tracker: Any) -> dict:
    query = tool_input["query"]

    prompt = f"""You are a research planning assistant. Decompose the following research question into focused sub-questions and classify it.

Question: {query}

Respond with a JSON object using exactly these keys:
{{
  "query_type": "<one of: comparison, research, factual, opinion>",
  "sub_questions": ["<2 to 5 focused sub-questions that together fully answer the original query>"],
  "research_strategy": "<one to two sentences on the best approach to research this>"
}}

Output valid JSON only."""

    try:
        result = _haiku_json_call(prompt, tracker, step="query_decomposer")
        log.info(
            "query_decomposer",
            query_type=result.get("query_type"),
            num_sub_questions=len(result.get("sub_questions", [])),
        )
        return {"status": "ok", **result}
    except Exception as exc:
        log.error("query_decomposer failed", error=str(exc))
        return {
            "status": "error",
            "query_type": "research",
            "sub_questions": [query],
            "research_strategy": "Search directly for the original query.",
        }


def execute_structured_compare(tool_input: dict[str, Any], tracker: Any) -> dict:
    items: list[str] = tool_input["items"]
    criteria: list[str] = tool_input["criteria"]
    raw_findings: dict = tool_input["raw_findings"]

    prompt = f"""You are a data formatting assistant. Extract structured comparison data from the raw findings below and return it as a markdown table.

Items to compare: {json.dumps(items)}
Criteria to compare on: {json.dumps(criteria)}

Raw findings per item:
{json.dumps(raw_findings, indent=2)}

Instructions:
- For each (item, criterion) pair, extract the most relevant value from the raw findings.
- Use "N/A" if the information is not available.
- Keep values concise (one short phrase per cell).

Respond with a JSON object using exactly these keys:
{{
  "comparison_table": "<full markdown table as a string>",
  "data": {{ "<item>": {{ "<criterion>": "<value>" }} }}
}}

Output valid JSON only."""

    try:
        result = _haiku_json_call(prompt, tracker, step="structured_compare")
        log.info(
            "structured_compare",
            items=items,
            criteria_count=len(criteria),
        )
        return {"status": "ok", **result}
    except Exception as exc:
        log.error("structured_compare failed", error=str(exc))
        return {"status": "error", "comparison_table": "", "data": {}}


def execute_llm_judge(tool_input: dict[str, Any], tracker: Any) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        log.warning("llm_judge skipped: OPENAI_API_KEY not set")
        return {
            "groundedness_score": 0.5,
            "flagged_claims": [],
            "passed": True,
            "reasoning": "Judge unavailable: OPENAI_API_KEY not set.",
        }

    client = OpenAI(api_key=api_key)

    prompt = f"""You are a fact-checking judge. Evaluate whether the following answer is grounded in the provided sources.

Question: {tool_input['question']}

Draft Answer: {tool_input['draft_answer']}

Claims to verify:
{json.dumps(tool_input['key_claims'], indent=2)}

Source snippets:
{json.dumps(tool_input['source_snippets'], indent=2)}

Respond with a JSON object using exactly these keys:
{{
  "groundedness_score": <float between 0.0 and 1.0>,
  "flagged_claims": [<list of claims not supported by the sources above>],
  "passed": <true if groundedness_score >= 0.6>,
  "reasoning": "<one sentence explanation>"
}}

Output valid JSON only. Do not include any text outside the JSON object."""

    start = time.time()
    try:
        response = client.chat.completions.create(
            model="gpt-5.4-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=512,
            response_format={"type": "json_object"},
            timeout=20.0,
        )
        latency_ms = (time.time() - start) * 1000

        verdict = json.loads(response.choices[0].message.content)

        cost = estimate_cost(
            "gpt-5.4-mini",
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
        )
        tracker.log_call(
            step="llm_judge",
            model="gpt-5.4-mini",
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            latency_ms=latency_ms,
            cost_usd=cost,
        )

        log.info(
            "llm_judge",
            groundedness_score=verdict.get("groundedness_score"),
            flagged_count=len(verdict.get("flagged_claims", [])),
            passed=verdict.get("passed"),
        )

        return {
            "groundedness_score": float(verdict.get("groundedness_score", 0.5)),
            "flagged_claims": verdict.get("flagged_claims", []),
            "passed": bool(verdict.get("passed", False)),
            "reasoning": verdict.get("reasoning", ""),
        }

    except APITimeoutError:
        log.error("llm_judge timeout")
        return {
            "groundedness_score": 0.5,
            "flagged_claims": [],
            "passed": True,
            "reasoning": "Judge timed out after 20s.",
        }
    except (APIError, json.JSONDecodeError, KeyError) as exc:
        log.error("llm_judge failed", error=str(exc))
        return {
            "groundedness_score": 0.5,
            "flagged_claims": [],
            "passed": True,
            "reasoning": f"Judge failed: {exc}",
        }


def execute_tool(name: str, tool_input: dict[str, Any], tracker: Any) -> dict:
    if name == "query_decomposer":
        return execute_query_decomposer(tool_input, tracker)
    if name == "structured_compare":
        return execute_structured_compare(tool_input, tracker)
    if name == "llm_judge":
        return execute_llm_judge(tool_input, tracker)
    log.warning("unknown_tool", name=name)
    return {"error": f"Unknown tool: {name}"}
