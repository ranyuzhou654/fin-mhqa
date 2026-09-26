"""Minimal observability without extra dependencies: Prometheus-text metrics and structured JSON logs.

Metric and log names mirror what an OpenTelemetry setup would export (one span per run, attributes run_id/user_id);
swapping in the OTel SDK only needs these two classes re-implemented.
"""
import json
import logging
import threading
import time

BUCKETS = (0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600)


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.counters, self.hist, self.gauges = {}, {}, {}

    def inc(self, name, value=1, **labels):
        key = (name, tuple(sorted(labels.items())))
        with self.lock:
            self.counters[key] = self.counters.get(key, 0) + value

    def observe(self, name, value, **labels):
        key = (name, tuple(sorted(labels.items())))
        with self.lock:
            h = self.hist.setdefault(key, {'count': 0, 'sum': 0.0, 'buckets': [0] * len(BUCKETS)})
            h['count'] += 1
            h['sum'] += value
            for i, b in enumerate(BUCKETS):
                if value <= b:
                    h['buckets'][i] += 1

    def gauge(self, name, fn):
        self.gauges[name] = fn

    @staticmethod
    def _labels(pairs, extra=()):
        pairs = list(pairs) + list(extra)
        return '{' + ','.join(f'{k}="{v}"' for k, v in pairs) + '}' if pairs else ''

    def render(self):
        lines = []
        with self.lock:
            for (name, labels), v in sorted(self.counters.items()):
                lines.append(f'finmhqa_{name}{self._labels(labels)} {v}')
            for (name, labels), h in sorted(self.hist.items()):
                for b, c in zip(BUCKETS, h['buckets']):
                    lines.append(f'finmhqa_{name}_bucket{self._labels(labels, [("le", b)])} {c}')
                lines.append(f'finmhqa_{name}_bucket{self._labels(labels, [("le", "+Inf")])} {h["count"]}')
                lines.append(f'finmhqa_{name}_sum{self._labels(labels)} {round(h["sum"], 3)}')
                lines.append(f'finmhqa_{name}_count{self._labels(labels)} {h["count"]}')
        for name, fn in sorted(self.gauges.items()):
            lines.append(f'finmhqa_{name} {fn()}')
        return '\n'.join(lines) + '\n'


def json_logger(name='finmhqa.service'):
    logger = logging.getLogger(name)

    def log(event, **fields):
        logger.info(json.dumps({'ts': round(time.time(), 3), 'event': event, **fields}, ensure_ascii=False))
    return log
