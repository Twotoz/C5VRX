# Fresh fine-lane range search evidence

C5VRX by Twotoz and contributors: https://github.com/Twotoz/C5VRX.
Official website and Discord: https://twotoz.github.io/C5VRX/.

[Protocol, counts and interpretation](../../SAFE_RANGE_STUDY.md).
Four completed1million per-run unique LUT/schedule-vector searches; cross-run
duplicates and observational equivalence remain possible. The large SQLite
leaderboards and numerical caches stay outside firmware; the tools reproduce
them. There is no global optimality or calibrated RF-range claim.

Each policy directory (`plain`, `context`, `confidence`, `compact`) contains:

- `search/`: launched protocol/source hashes, completed counts and frozen
  short-screen leaders.
- `refinement/`: equal capped fitting protocol, refined candidates, frozen
  full-video finalists and counts.
- `assembly.json`: real ESP32-C5 assembler/target/source/binary hashes and costs.
- `confirmation/`: fresh full-field selection, frozen winner, available
  independent frame rows and decision. A baseline-identity rejection records
  `final: null`; selection rows are never relabelled as final confirmation.
- `proxy_convergence.json`: training score improvements versus unique vector
  evaluations, proxy Pareto frontier and matched RANGE32 training control.

The first confidence validator started before the compiled-baseline early-exit
optimization and completes the explicit duplicate comparison. Later validators
may reject an identical baseline without consuming fresh final datasets.
Source hashes distinguish these paths; neither produces a new confirmed model.

Only individually confirmed new models may enter the predeclared fresh
shared-input selection/confirmation. An empty shared frozen set means that
comparison is skipped, retaining RANGE32. Synthetic acceptance, assembler
acceptance and physical picture/range acceptance are separate evidence.
