"""Run the agent service.

  Real agent (needs the retrieval server on 8100 and model quota):
    PYTHONPATH=src .venv/bin/python scripts/serve.py --agent graph:configs/bench_b3_graph_repair_v2_planner_v2_ctx.json
  Recorded trajectories instead of model calls (demo, tests, load tests; no quota):
    PYTHONPATH=src .venv/bin/python scripts/serve.py --agent "cassette:docs/examples/*" --speed 0.2
Create users with scripts/service_admin.py; keys are printed once and stored hashed.
"""
import argparse
import glob
import logging
from pathlib import Path
import uvicorn
from finmhqa.service.agents import CassetteAgent, GraphAgent
from finmhqa.service.app import create_app
from finmhqa.service.observability import Metrics, json_logger
from finmhqa.service.scheduler import Scheduler
from finmhqa.service.store import Store


def build(args):
    kind, _, spec = args.agent.partition(':')
    if kind == 'graph':
        agent = GraphAgent(spec, url=args.retrieval_url)
    elif kind == 'cassette':
        cases = sorted(p for p in glob.glob(spec) if (Path(p) / 'trajectory.jsonl').exists())
        agent = CassetteAgent(cases, speed=args.speed)
    else:
        raise SystemExit('--agent must be graph:<config> or cassette:<glob>')
    store, metrics, log = Store(args.db), Metrics(), json_logger()
    scheduler = Scheduler(store, agent, metrics=metrics, workers=args.workers, max_queue=args.max_queue,
                          run_timeout=args.run_timeout, log=log)
    return create_app(store, scheduler, metrics, heartbeat=args.heartbeat, log=log)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--agent', required=True)
    p.add_argument('--db', default='data/service/service.db')
    p.add_argument('--speed', type=float, default=1.0, help='cassette delay multiplier (0.1 = 10x faster)')
    p.add_argument('--workers', type=int, default=2, help='concurrent agent runs (the retrieval server has one slot)')
    p.add_argument('--max-queue', type=int, default=100)
    p.add_argument('--run-timeout', type=float, default=900)
    p.add_argument('--heartbeat', type=float, default=15)
    p.add_argument('--retrieval-url', default='http://127.0.0.1:8100')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=8200)
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    uvicorn.run(build(a), host=a.host, port=a.port, log_level='warning')


if __name__ == '__main__':
    main()
