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
