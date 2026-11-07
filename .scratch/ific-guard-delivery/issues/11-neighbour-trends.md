# 11 Neighbour trends and anomalies

Status: ready-for-agent
Type: task
Size: S
Blocked by: 08
Owns: `ific_guard/terrestrial/trends.py`, `tests/test_trends.py`

## Goal

The brief asks the side quest to "surface a small number of trends or anomalies in neighboring countries administrations' notices, such as new activity by band, geography, station class or bandwidth."

## Do

1. Using ticket 08's adapter, aggregate notices from `settings.neighbour_adms` (Russia, Sweden, Estonia, Norway, Denmark) over the issues available: at least 3075 and 3076 from the shared data, more from sync if they are already stored. With only some countries notifying in these issues, state which had no activity rather than leaving them out.
2. Report 3-5 specific observations, with counts, the issues compared, and how to reproduce each.
3. Add a read-only function and an output section for ticket 14's report.

Use simple, explainable statistics. With so few issues, a "trend" is weak evidence, so say how many issues back each observation and avoid predictions.

## Acceptance

- Every number can be reproduced from the listed notices by a test or command.
- No observation is presented as a concern unless it is linked to a finding.

## Not in scope

Charts beyond what ticket 12 needs, and any machine-learning model.
