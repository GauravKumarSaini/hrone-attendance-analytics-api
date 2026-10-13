# DECISIONS.md

## 1. Indexes

I created a unique index on `employees.emp_code` to prevent duplicate employees and a unique compound index on `attendance_logs (emp_code, date)` to prevent duplicate daily attendance records. I also added an index on `employees (department, emp_code)` for department-based employee queries, an index on `employees.joined_on` for joining-date filtering, and an index on `attendance_logs (date, emp_code)` for date-based attendance queries. I avoided adding more indexes without evidence that they would improve query performance.

## 2. Punch-in race

Two identical requests may both check for an existing attendance record before either inserts it. The unique compound index ensures that only one record can be inserted for the same employee and attendance date. The other request should receive HTTP 409 when its insert raises `DuplicateKeyError`, provided the exception is caught and handled by the endpoint.

## 3. Ties

The leaderboard is intended to use competition ranking. Employees with equal total late minutes receive the same rank, and the next rank skips accordingly (for example, 1, 2, 2, 4). The limit should be applied after ranking so employees tied at the cutoff are included. Tie-at-limit behavior should be verified with a test case.

## 4. Headcount

The department summary starts with eligible employees and looks up their attendance records. Therefore, employees who have joined by the end of the selected month are counted in the department headcount even if they have no attendance logs.

## 5. Scaling

For 100 times more data, I would run automated performance tests using a representative large dataset, inspect query execution plans, and optimize aggregation pipelines and indexes based on measured performance. I would also verify that the explain endpoint reflects the actual queries executed by each API endpoint.