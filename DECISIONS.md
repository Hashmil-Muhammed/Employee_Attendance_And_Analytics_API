# DECISIONS.md

1. **Indexes.**
I created:
- `employees`: `{emp_code: 1}` (unique, fast lookups), `{department: 1, joined_on: 1}` (department filters/trends), and `{joined_on: 1}` (headcount queries).
- `attendance_logs`: `{emp_code: 1, date: 1}` (unique, natural-key lookups), and `{date: -1, emp_code: 1}` (sorting/leaderboard).
Rejected indexing `department` on `attendance_logs`: department lives on `employees`, where cross-collection pipelines start, avoiding denormalization.

2. **Punch-in race.**
Punch-in runs `attendance_logs.insert_one()`. The unique compound index on `{emp_code, date}` allows only one insert to succeed. Competing requests trigger a `DuplicateKeyError`, which the API handles by returning HTTP 409 Conflict, preventing check-then-insert races.

3. **Ties.**
The leaderboard uses `$setWindowFields` with `$rank` sorted by late minutes descending, producing competition ranking (1, 2, 2, 4). The rank cutoff filter (`rank <= limit`) includes all employees tied at the boundary, sorted secondarily by `emp_code`.

4. **Headcount.**
Department summary starts at `employees` with `$match: {joined_on: {$lte: end_date}}` to establish baseline headcount, then `$lookup` joins `attendance_logs`. Employees with zero logs produce an empty array but are still counted toward headcount.

5. **One thing you would change.**
At 100x scale, real-time `$lookup` and `$densify` aggregations over large datasets present scalability concerns. I would introduce pre-aggregated daily rollups or materialized views maintained asynchronously via change streams.
