# Agent blue reviews

These reviews describe conditional attack paths in the declared Metropolis
water-treatment testbed. They are static assessments; they do not establish
live firewall enforcement, successful access, or physical process effects.

| Run | Results | Record |
|---|---|---|
| [2026-11-19 fresh exploratory review](metropolis/water_treatment_v1/20261119T055618Z_12c8c311/scenarios.md) | Six conditional paths, nine catalog families, and ten trust-boundary paths from current source | [JSON](metropolis/water_treatment_v1/20261119T055618Z_12c8c311/scenarios.json) · [metadata](metropolis/water_treatment_v1/20261119T055618Z_12c8c311/review-metadata.json) |
| [2026-11-19 focused review](metropolis/water_treatment_v1/20261119T040839Z_4ac86bb/scenarios.md) | Five node-to-node chains, with static firewall and credential assessment | [JSON](metropolis/water_treatment_v1/20261119T040839Z_4ac86bb/scenarios.json) · [metadata](metropolis/water_treatment_v1/20261119T040839Z_4ac86bb/review-metadata.json) |
| [2026-11-19 initial review](metropolis/water_treatment_v1/20261119T034401Z_4ac86bb/scenarios.md) | Earlier scenario review | [JSON](metropolis/water_treatment_v1/20261119T034401Z_4ac86bb/scenarios.json) · [metadata](metropolis/water_treatment_v1/20261119T034401Z_4ac86bb/review-metadata.json) |

Use the fresh review for the latest static assessment. The earlier reviews
remain historical records.

Future agent-authored scenario reviews go under
`metropolis/<dataset>/<UTC timestamp>_<revision>/`. The on-demand Codex
Security review tool writes new runs under `security-reviews/`.
