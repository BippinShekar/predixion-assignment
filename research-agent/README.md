# Research Agent

A production-oriented AI research agent that answers open-ended research questions with
structured, source-backed output. Built as a focused single-agent system using the
Anthropic Claude API, OpenAI API, and custom tool orchestration.

---

## Setup

**Requirements:** Python 3.9+, an Anthropic API key, and an OpenAI API key.

```bash
git clone <repo-url>
cd research-agent

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env and fill in ANTHROPIC_API_KEY and OPENAI_API_KEY
```

**Run a query:**

```bash
python3 main.py "Compare the top 3 open-source vector databases for RAG products."

# JSON output (for piping or programmatic use):
python3 main.py "your question" --json

# Suppress the trace summary:
python3 main.py "your question" --no-trace
```

---

## Architecture

```
User Query (CLI)
      |
      v
Orchestrator (claude-sonnet-4-6)
  - Holds all tools
  - Drives the loop autonomously
  - Decides what to call and when
      |
      |-- web_search (built-in, server-side)
      |-- query_decomposer (Haiku call)
      |-- structured_compare (Haiku call)
      |-- llm_judge (gpt-5.4-mini call)
      |-- submit_answer (terminates loop)
      |
      v
Pydantic validation
      |
      v
Final output (pretty-print or JSON)
```

The orchestrator is a simple `while` loop: call Claude, execute any tool use blocks,
feed results back, repeat until `submit_answer` is called or `MAX_ITERATIONS` is hit.
Claude decides which tools to call, in what order, and how many times. The code has no
hardwired sequence.

---

## Tools

| Tool | Model | Purpose |
| --- | --- | --- |
| `web_search` | Claude built-in | Searches the web. Executed server-side by Anthropic. No client parsing required. |
| `query_decomposer` | claude-haiku-4-5 | Breaks a complex query into sub-questions and classifies the query type. Uses a dedicated LLM call so the decomposition is not just a side-effect of orchestrator reasoning. |
| `structured_compare` | claude-haiku-4-5 | Takes raw freeform findings per item and normalizes them into a structured markdown comparison table. Required for comparison queries where raw search results are unstructured. |
| `llm_judge` | gpt-5.4-mini | Cross-model groundedness check. Takes the draft answer and source snippets, returns a 0.0-1.0 groundedness score and a list of unsupported claims. Using a different model (OpenAI vs Anthropic) avoids same-model blind spots. |
| `submit_answer` | n/a | Structured terminal tool. When Claude calls this, the loop exits and the input is validated against the Pydantic schema. Forces a deterministic output shape regardless of what Claude produces. |

`query_decomposer` and `structured_compare` use Haiku rather than the full Sonnet model
because these are focused, constrained tasks. Haiku is 3x cheaper and significantly faster
for structured extraction with a clear prompt.

---

## Why an Agentic Approach

Research queries require adaptive information gathering. You do not know upfront which
searches will yield useful results, how many searches are needed, or whether a comparison
table is warranted until you start reading the results. A static pipeline would need to
handle all of this with branching logic hardcoded in the orchestrator.

An agent loop lets the system follow leads, discard dead ends, run extra searches when
initial results are weak, and skip tools that are not relevant to the query type. A
comparison query triggers `structured_compare`; a factual lookup does not. The agent
decides, not the pipeline.

The tradeoff is reduced predictability and higher cost per query. For research tasks where
answer quality matters more than strict latency or cost budgets, this is the right call.

---

## Handling Bad Tool Results

Every tool has a try/except that returns a safe fallback dict rather than raising. Specific cases:

- `query_decomposer` failure: returns the original query as a single sub-question, research continues
- `structured_compare` failure: returns empty table, agent continues with unformatted findings
- `llm_judge` timeout or API error: returns `groundedness_score: 0.5, passed: True` so the loop is not blocked; the failure reason is written to `reasoning` and surfaces in the output
- `submit_answer` with invalid Pydantic data: raises immediately so the caller sees a real error rather than a silently malformed result

All tool outputs are sanitized before being added to the message history: injection-pattern
strings are redacted and outputs are truncated at 8,000 characters.

---

## Reducing Hallucinations

Three mechanisms work together:

1. **Source grounding in the system prompt.** Claude is instructed to only include claims
   it can back with a source URL. The `key_findings` schema requires at least one
   `source_url` per claim.

2. **llm_judge cross-validation.** Before submitting, Claude must call `llm_judge` with
   its draft answer and the source snippets. A second model (gpt-5.4-mini) independently
   checks whether each claim is supported. Claims flagged as unsupported must be removed
   or qualified before submission. Claude sees the judge result and adjusts confidence.

3. **Prompt injection guard.** Web page content is treated as untrusted input. Before any
   tool result enters the message history, `_sanitize_tool_output` strips instruction-like
   patterns ("ignore previous instructions", "new instructions:", etc.) and truncates
   excessively long content.

---

## Production Readiness

What this system does today that production systems need:

- Structured output with Pydantic validation (no freeform string parsing)
- Per-call logging with trace IDs, token counts, latency, and cost estimates
- Retry logic with exponential backoff for timeouts and 60-second waits on rate limits
- Graceful degradation: every failure path returns a valid `ResearchResult` with
  `confidence: low` and honest limitations rather than an exception
- Hard token budget cap (300k tokens per query) that aborts the loop before hitting API limits

What would need to change before production:

- **Pre-flight cost estimation.** The current token budget aborts mid-loop rather than
  before the first call. A pre-flight estimate based on query complexity would prevent
  spending on queries projected to exceed the budget.
- **Context compression.** At iteration 2+, the message history is already 60-90k tokens.
  Summarizing earlier search results before appending new ones would cut costs by 40-60%
  and make the system more stable on accounts with token-per-minute limits.
- **Async tool execution.** `query_decomposer` and `web_search` sub-questions could be
  parallelized. Currently everything is sequential.
- **Caching.** Identical or near-identical queries should return cached results. A simple
  Redis TTL cache on the question hash would cover repeated queries.
- **Observability.** Replace structlog to stderr with a proper trace exporter
  (Langfuse, Helicone, or OpenTelemetry). The trace_id and per-call data are already
  structured for this.
- **Guardrails.** Answers with `confidence: low` or `judge_verdict.passed: false` should
  trigger a human review queue rather than being returned directly to users.
- **Rate limit awareness.** The current retry waits 60 seconds on every 429. A smarter
  implementation would estimate the wait from the token usage headers and only wait as
  long as needed.

---

## What to Monitor in Production

| Signal | Why |
| --- | --- |
| Judge groundedness score distribution | Drift toward lower scores indicates search quality degradation or prompt issues |
| Iterations per query | High iteration counts signal the agent is struggling to find information |
| Tool call frequency per query | Unexpected tool call patterns may indicate prompt drift |
| Token cost per query | Spikes indicate web search is returning unusually large pages |
| Confidence level distribution | A rising proportion of "low" confidence answers signals data source problems |
| Rate limit frequency | Indicates need for higher tier or context compression |
| Queries that exceed MAX_ITERATIONS | Direct signal of unanswerable queries or agent loop bugs |

---

## Key Tradeoffs

**Agentic loop vs. fixed pipeline.** The loop is flexible and produces higher-quality
answers by adapting to what it finds. The cost is unpredictability: query cost and latency
vary significantly. A pipeline would be cheaper and faster but would require hardcoded
branching for every query type.

**Haiku for sub-tools vs. Sonnet everywhere.** Using Haiku for `query_decomposer` and
`structured_compare` cuts cost significantly for these focused tasks. The risk is that
Haiku occasionally produces malformed JSON (seen once during testing with large outputs).
The fallback handles this but quality degrades silently.

**gpt-5.4-mini as judge vs. same model.** Cross-model validation catches blind spots that
a same-model judge would miss. The cost is a second API dependency and additional latency
(2-3 seconds). A production system could make the judge optional for low-stakes queries.

**No frontend.** The assignment explicitly devalues UI. A clean CLI with `--json` output
is more useful for integration than a web interface for a system that is fundamentally
an API service.

**No caching in this version.** Adding a Redis TTL cache would be straightforward given
the trace architecture, but adds infrastructure complexity not warranted for a prototype.
The tradeoff is explicit in the code as a comment-free gap.

---

## Known Limitations

- Token consumption per query is high (80-130k tokens on accounts with web search)
- The 30k token/minute rate limit on free-tier Anthropic accounts makes batch use
  impractical without delays between queries
- `structured_compare` can fail on very large comparison sets if the Haiku output
  exceeds 4096 tokens (the current cap)
- The agent has no memory across queries; each run is stateless
- The judge is skipped gracefully but the confidence score does not automatically
  drop when the judge is unavailable
- No citation deduplication: the same URL can appear in multiple findings

---

## Future Improvements

- Pre-flight token budget estimation with abort on projected overspend
- Context compression: summarize earlier search rounds before appending new ones
- Async parallel sub-question searches
- Redis caching layer with configurable TTL per query type
- Langfuse integration for full trace visualization
- Human review queue for low-confidence answers
- Streaming output so users see progress during long runs
- Unit tests for tool executors and schema validation
