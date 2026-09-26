# Shared driver step for benchmark runs (sourced by run_*.sh). Usage: bench_run <out_dir> <run_agent_dev args...>
# - a finished run (COMPLETE, or a legacy run without run_status.json) is skipped;
# - an unfinished run is continued with --resume (only missing / partial / infrastructure-failed items);
# - runner exit 3 = quota or billing refusal: the whole driver stops so no further runs burn through errors.
bench_run() {
  local out=$1; shift
  local resume=()
  if [ -d $out ]; then
    # legacy runs (before the harness) have health_after.json but no run_status.json
    if { [ ! -f $out/run_status.json ] && [ -f $out/health_after.json ]; } || grep -qs '"status": "COMPLETE"' $out/run_status.json; then
      echo "skip $out"; return 0
    fi
    resume=(--resume); echo "resume $out"
  fi
  PYTHONPATH=src .venv/bin/python scripts/run_agent_dev.py "$@" --out $out $resume >> $out.log 2>&1
  local code=$?
  echo "done $out exit $code"
  if [ $code -eq 3 ]; then echo "STOP: model quota or billing refused requests; rerun this driver after the quota resets"; exit 3; fi
  PYTHONPATH=src:scripts .venv/bin/python scripts/audit_agent_run.py --run $out --out $out.audit.json.tmp && mv $out.audit.json.tmp $out.audit.json
}

# Provider drift gate (roadmap P1.4): run once per driver before any batch; CANARY=0 skips it.
# Exit 3 (quota) or 1 (drift suspected) stops the driver before quota is spent on a whole batch.
canary_gate() {
  [ "${CANARY:-1}" = "0" ] && { echo "canary skipped (CANARY=0)"; return 0; }
  PYTHONPATH=src .venv/bin/python scripts/canary.py --config configs/bench_$1.json
  local code=$?
  if [ $code -ne 0 ]; then echo "STOP: canary failed (exit $code); inspect data/bench/canary/ before running the batch"; exit $code; fi
}

# Cost gate (roadmap P1.5): only when TOKEN_LIMIT is set; estimate from past runs of the same config.
cost_gate() {  # config stem, planned item-runs
  [ -z "${TOKEN_LIMIT:-}" ] && return 0
  PYTHONPATH=src .venv/bin/python scripts/run_registry.py > /dev/null || exit 1
  PYTHONPATH=src .venv/bin/python scripts/cost_estimate.py --config configs/bench_$1.json --items $2 --limit-tokens $TOKEN_LIMIT || { echo "STOP: over TOKEN_LIMIT"; exit 1; }
}
