# Agent blue reviews

These reviews describe conditional attack paths in the declared Metropolis
water-treatment testbed. They are static assessments; they do not establish
live firewall enforcement, successful access, or physical process effects.

| Run | Results | Record |
|---|---|---|
| [2026-11-19 focused review](metropolis/water_treatment_v1/20261119T040839Z_4ac86bb/scenarios.md) | Five node-to-node chains, with static firewall and credential assessment | [JSON](metropolis/water_treatment_v1/20261119T040839Z_4ac86bb/scenarios.json) · [metadata](metropolis/water_treatment_v1/20261119T040839Z_4ac86bb/review-metadata.json) |
| [2026-11-19 initial review](metropolis/water_treatment_v1/20261119T034401Z_4ac86bb/scenarios.md) | Earlier scenario review | [JSON](metropolis/water_treatment_v1/20261119T034401Z_4ac86bb/scenarios.json) · [metadata](metropolis/water_treatment_v1/20261119T034401Z_4ac86bb/review-metadata.json) |

The focused review corrects the initial review's firewall conclusion:
engineering-to-dosing Modbus and attack-lab-to-OT new traffic are blocked by
the generated baseline **if it is applied**. Keep the earlier review as a
historical record, and use the focused review for that conclusion.

Future agent-authored scenario reviews go under
`metropolis/<dataset>/<UTC timestamp>_<revision>/`. The on-demand Codex
Security review tool writes new runs under `security-reviews/`.
