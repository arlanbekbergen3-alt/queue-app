from collections import defaultdict
from collections.abc import Callable

BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


class Metrics:
    def __init__(self) -> None:
        self.requests: dict[str, int] = defaultdict(int)
        self.bucket_counts = [0] * len(BUCKETS)
        self.duration_sum = 0.0
        self.duration_count = 0
        self.counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, Callable[[], float]] = {}
        self._counter_sources: dict[str, Callable[[], float]] = {}

    def observe_request(self, status: int, seconds: float) -> None:
        self.requests[f"{status // 100}xx"] += 1
        self.duration_sum += seconds
        self.duration_count += 1
        for i, bound in enumerate(BUCKETS):
            if seconds <= bound:
                self.bucket_counts[i] += 1

    def inc(self, name: str, amount: int = 1) -> None:
        self.counters[name] += amount

    def gauge(self, name: str, source: Callable[[], float]) -> None:
        self._gauges[name] = source

    def counter_source(self, name: str, source: Callable[[], float]) -> None:
        self._counter_sources[name] = source

    def render(self) -> str:
        lines = ["# TYPE queue_http_requests_total counter"]
        for klass in sorted(self.requests):
            lines.append(f'queue_http_requests_total{{status="{klass}"}} {self.requests[klass]}')
        lines.append("# TYPE queue_http_request_duration_seconds histogram")
        for bound, count in zip(BUCKETS, self.bucket_counts, strict=True):
            lines.append(f'queue_http_request_duration_seconds_bucket{{le="{bound}"}} {count}')
        lines.append(
            f'queue_http_request_duration_seconds_bucket{{le="+Inf"}} {self.duration_count}'
        )
        lines.append(f"queue_http_request_duration_seconds_sum {self.duration_sum:.6f}")
        lines.append(f"queue_http_request_duration_seconds_count {self.duration_count}")
        for name in sorted({*self.counters, *self._counter_sources}):
            value: float = self.counters.get(name, 0)
            if name in self._counter_sources:
                value += self._counter_sources[name]()
            lines.append(f"# TYPE queue_{name} counter")
            lines.append(f"queue_{name} {value}")
        for name in sorted(self._gauges):
            lines.append(f"# TYPE queue_{name} gauge")
            lines.append(f"queue_{name} {self._gauges[name]()}")
        return "\n".join(lines) + "\n"


metrics = Metrics()
