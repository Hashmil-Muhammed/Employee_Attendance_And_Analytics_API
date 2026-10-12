# DECISIONS.md

1. **Indexes.** 
I created:
- `employees: {emp_code: 1}` (Unique): Guarantees unique employee codes and accelerates lookups.
- `attendance_logs: {emp_code: 1, date: 1}` (Unique): Prevents duplicate records per employee per day and accelerates natural-key lookups.
- `attendance_logs: {date: -1, emp_code: 1}`: Optimizes the `/attendance` endpoint's default pagination and sorting strategy.
I considered but rejected indexing `department` on `attendance_logs`. Because the schema places `department` entirely within `employees`, cross-collection aggregations must start at `employees` anyway, making a denormalized index unnecessary.

2. **Punch-in race.** 
The endpoint builds the document and executes `db.attendance_logs.insert_one()`. Relying on the unique compound index on `{emp_code, date}`, the first insertion succeeds instantly (returning 201). The second simultaneous insertion triggers a strict `DuplicateKeyError` from MongoDB at the storage level, which the code catches and handles by returning a 409 Conflict. This cleanly avoids check-then-insert races.

3. **Ties.** 
The late leaderboard utilizes MongoDB's `$setWindowFields` and `$rank` operators, sorting strictly by `total_late_minutes: -1`. This enforces standard competition ranking natively (e.g. 1, 2, 2, 4). The `limit` acts on this computed rank (`$match: {rank: <= limit}`). Therefore, if employees tie exactly at the cutoff rank, all tied employees are returned, ordered secondarily by `emp_code` ascending.

4. **Headcount.** 
The department summary aggregation pipelines begin by querying the `employees` collection directly with `$match: {joined_on: {$lte: end_date}}`. This establishes the correct headcount baseline immediately. Only then does it execute a `$lookup` (a left-join) into `attendance_logs`. If an employee has zero logs, the lookup array is empty, but the employee still contributes fully to the `$size` evaluation of the department's headcount.

5. **One thing you would change.** 
At 100x scale, real-time analytics aggregations involving unbounded `$lookup` and `$densify` operations across millions of logs would timeout. I would implement materialized views or pre-aggregated rollups (e.g., emitting daily department totals asynchronously) maintained via change streams, rather than computing raw logs on the fly per API request.
