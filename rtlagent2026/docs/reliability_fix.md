# Reliability fixes: contribution and reproduction

Base: `tombossking_test`, commit `868b44dc1e0ffb0e951394462978e4fe66566eb9` (PR #4 head). This is a reliability patch, not a new competition score claim. Official baseline, official example, model weights, and teammate skill files are unchanged.

## Changes

- Classify explicit license failures, not every mention of `license`. Valid-license banners and normal license release are accepted.
- Reject nonzero simulator exits and failure/error markers before inspecting a success result.
- Require exactly one terminal line `TB_RESULT checks=<positive integer> errors=0`. Legacy `TB_SUCCESS`, zero checks, and missing/duplicate results do not establish functional correctness.
- Remove the generic X-only fallback. Legacy task-specific fixtures are restricted to mock smoke tests, not used as a real-backend task oracle.
- Generate reference tests from specification without feeding the DUT implementation to the reference-generation prompt.
- Record L1 only after successful lint, reset rollback history per solve, and stop on tool-environment failures without rewriting the DUT. Missing functional verification preserves the compiled candidate at internal milestone 1.
- Label milestones as internal, not official scoring results.
- Share the lint stage budget across compile/elaborate; subtract testbench-generation time from simulation budget.
- Support explicit `VIVADO_BIN` on Windows/Linux and Windows batch launching.

## Run unit tests (Python 3.10+ recommended)

From `rtlagent2026/`:

```sh
python -B scripts/run_reliability_tests.py
```

Native tests are skipped unless opted in. This command does not need a model or GPU service.

## Native Vivado 2026.1 tests

Windows PowerShell, from `rtlagent2026/`:

```powershell
$env:VIVADO_BIN = 'D:\AMDDesignTools\2026.1\Vivado\bin'
python -B scripts/run_reliability_tests.py --native
```

Linux, from `rtlagent2026/`:

```sh
source /actual/Vivado/2026.1/settings64.sh
python3 -B scripts/run_reliability_tests.py --native
```

The native tests simulate an independent exhaustive 8-bit inverter oracle against a correct implementation and a deliberately wrong stuck-zero implementation, then synthesize the correct implementation for the existing ZU3EG target. Test RTL/reference fixtures are confined to tests and never passed into the agent as task answers.

`experiments/reliability_fix/results.json` records test outcomes and source hashes. Tests use stdlib `unittest`; normal package imports still rely on the existing project structure.

## Limitations / next work

A reported comparison count and a regex-based TB shape check do not prove the oracle is semantically correct. Model-generated reference code may still misinterpret a specification, falsely report counts, or miss corner cases. Independent externally judged tasks, broader mutation tests, and coverage remain necessary. A no-verdict simulator run is classified as unverified, not asserted to be a license fault without evidence.

This patch intentionally does NOT fix signed/parameterized interface parsing, helper-module extraction, deployment model/context inconsistencies, startup/health readiness, or the shell safety-net issue. Deadline accounting is improved, not a proof of global hard cancellation. AMD ROCm/offline Docker and model-quality/skill-gain experiments have not been performed here. Existing speculative scores in REPORT.md are not validated by these regression tests.

Conservative handling can reduce apparent internal L2/L3 counts. That is expected: the goal is to stop claiming unsupported successes, not manufacture a higher score. Do not report these tests as 29 contest tasks passed.
