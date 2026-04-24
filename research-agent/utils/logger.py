import time
import uuid
from dataclasses import dataclass, field

import structlog


def setup_logger() -> None:
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


def get_logger() -> structlog.BoundLogger:
    return structlog.get_logger()


@dataclass
class CallRecord:
    step: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float
    timestamp: str


@dataclass
class CallTracker:
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    calls: list[CallRecord] = field(default_factory=list)

    def log_call(
        self,
        step: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        cost_usd: float,
    ) -> None:
        record = CallRecord(
            step=step,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self.calls.append(record)

        get_logger().info(
            "llm_call",
            trace_id=self.trace_id,
            step=step,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=round(latency_ms, 1),
            cost_usd=round(cost_usd, 6),
        )

    def summary(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "total_calls": len(self.calls),
            "total_input_tokens": sum(c.input_tokens for c in self.calls),
            "total_output_tokens": sum(c.output_tokens for c in self.calls),
            "total_cost_usd": round(sum(c.cost_usd for c in self.calls), 6),
            "total_latency_ms": round(sum(c.latency_ms for c in self.calls), 1),
            "calls": [vars(c) for c in self.calls],
        }

    def print_summary(self) -> None:
        s = self.summary()
        print(f"\n{'=' * 52}")
        print(f"TRACE  {s['trace_id']}")
        print(f"Calls  {s['total_calls']}")
        print(f"Tokens {s['total_input_tokens']} in / {s['total_output_tokens']} out")
        print(f"Cost   ${s['total_cost_usd']:.6f}")
        print(f"Time   {s['total_latency_ms']:.0f}ms")
        print(f"{'=' * 52}\n")
