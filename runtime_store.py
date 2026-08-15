import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit


_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("APP_DB_PATH", os.path.join(_PROJECT_DIR, "data", "runtime.db"))
_SCHEMA_LOCK = threading.Lock()
_INITIALIZED = False
_SENSITIVE_KEYS = ("password", "passwd", "secret", "token", "apikey", "api_key", "authorization")
ACTIVE_INSTANCE_STATUSES = ("queued", "running", "waiting_alist")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sanitize_url(value):
    if not value:
        return value
    try:
        parts = urlsplit(str(value))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except ValueError:
        return str(value)[:1000]


def sanitize_payload(value, max_string_length=20000):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            if any(marker in key_text.lower() for marker in _SENSITIVE_KEYS):
                result[key_text] = "***"
            else:
                result[key_text] = sanitize_payload(item, max_string_length)
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize_payload(item, max_string_length) for item in value]
    if isinstance(value, str):
        if len(value) > max_string_length:
            return value[:max_string_length] + "…(truncated)"
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if hasattr(value, "__dict__"):
        return sanitize_payload(vars(value), max_string_length)
    return str(value)[:max_string_length]


def _json_dump(value):
    if value is None:
        return None
    return json.dumps(sanitize_payload(value), ensure_ascii=False, separators=(",", ":"))


def _connect():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _ensure_column(connection, table, column, definition):
    columns = {
        row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
    }
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    global _INITIALIZED
    if _INITIALIZED:
        return

    with _SCHEMA_LOCK:
        if _INITIALIZED:
            return

        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with _connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS api_requests (
                    request_id TEXT PRIMARY KEY,
                    route TEXT NOT NULL,
                    method TEXT NOT NULL,
                    task_uuid TEXT,
                    payload_json TEXT,
                    response_json TEXT,
                    status_code INTEGER,
                    received_at TEXT NOT NULL,
                    finished_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_runs (
                    run_id TEXT PRIMARY KEY,
                    task_uuid TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    request_id TEXT,
                    status TEXT NOT NULL,
                    parameters_json TEXT,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    postprocess_status TEXT,
                    postprocess_started_at TEXT,
                    postprocess_finished_at TEXT,
                    FOREIGN KEY (request_id) REFERENCES api_requests(request_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS callback_attempts (
                    callback_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    service TEXT NOT NULL,
                    target TEXT,
                    status TEXT NOT NULL,
                    request_json TEXT,
                    response_json TEXT,
                    status_code INTEGER,
                    duration_ms INTEGER,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES task_runs(run_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS alist_copy_tasks (
                    alist_task_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    source_dir TEXT,
                    destination_dir TEXT,
                    entry_name TEXT,
                    name TEXT,
                    state INTEGER,
                    status TEXT,
                    progress REAL,
                    start_time TEXT,
                    end_time TEXT,
                    total_bytes INTEGER,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_seen_at TEXT,
                    completed_at TEXT,
                    FOREIGN KEY (run_id) REFERENCES task_runs(run_id)
                )
                """
            )
            _ensure_column(connection, "task_runs", "postprocess_status", "TEXT")
            _ensure_column(connection, "task_runs", "postprocess_started_at", "TEXT")
            _ensure_column(connection, "task_runs", "postprocess_finished_at", "TEXT")
            _ensure_column(connection, "alist_copy_tasks", "last_seen_at", "TEXT")
            _ensure_column(connection, "alist_copy_tasks", "completed_at", "TEXT")
            connection.execute(
                """
                UPDATE alist_copy_tasks
                SET last_seen_at = COALESCE(last_seen_at, updated_at, created_at)
                WHERE last_seen_at IS NULL
                """
            )
            connection.execute(
                """
                UPDATE alist_copy_tasks
                SET completed_at = COALESCE(completed_at, end_time, updated_at)
                WHERE completed_at IS NULL AND state IN (2, 4, 7)
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_runs_created_at ON task_runs(created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_runs_status ON task_runs(status)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_runs_request_id ON task_runs(request_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_requests_received_at ON api_requests(received_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_callback_attempts_created_at ON callback_attempts(created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_alist_copy_tasks_run_id ON alist_copy_tasks(run_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_alist_copy_tasks_state ON alist_copy_tasks(state)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_runs_postprocess_status "
                "ON task_runs(postprocess_status)"
            )
            connection.execute("PRAGMA optimize")
            connection.execute(
                """
                UPDATE task_runs
                SET status = 'interrupted',
                    error = COALESCE(error, 'Application restarted before the run completed'),
                    finished_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (utc_now(),),
            )
            connection.execute(
                """
                UPDATE task_runs
                SET postprocess_status = 'pending',
                    postprocess_started_at = NULL
                WHERE postprocess_status = 'running'
                """
            )
        _INITIALIZED = True


def create_api_request(route, method, payload=None, task_uuid=None):
    init_db()
    request_id = str(uuid.uuid4())
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO api_requests (
                request_id, route, method, task_uuid, payload_json, received_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (request_id, route, method, task_uuid, _json_dump(payload), utc_now()),
        )
    return request_id


def finish_api_request(request_id, status_code, response=None):
    if not request_id:
        return
    init_db()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE api_requests
            SET status_code = ?, response_json = ?, finished_at = ?
            WHERE request_id = ?
            """,
            (status_code, _json_dump(response), utc_now(), request_id),
        )


def create_run(task_uuid, task_type, trigger_type, parameters=None, request_id=None):
    init_db()
    run_id = str(uuid.uuid4())
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO task_runs (
                run_id, task_uuid, task_type, trigger_type, request_id,
                status, parameters_json, created_at
            ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
            """,
            (
                run_id,
                str(task_uuid),
                task_type,
                trigger_type,
                request_id,
                _json_dump(parameters),
                utc_now(),
            ),
        )
    return run_id


def create_run_if_instance_idle(
    task_uuid,
    task_type,
    trigger_type,
    parameters=None,
    request_id=None,
):
    """Atomically create a queued run unless the same task instance is active."""
    init_db()
    run_id = str(uuid.uuid4())
    task_uuid = str(task_uuid)
    now = utc_now()
    placeholders = ",".join("?" for _ in ACTIVE_INSTANCE_STATUSES)

    with _connect() as connection:
        # Serialize the active-run check and insert so simultaneous scheduler
        # and API triggers cannot both enqueue the same task instance.
        connection.execute("BEGIN IMMEDIATE")
        active_run = connection.execute(
            f"""
            SELECT run_id, status, created_at
            FROM task_runs
            WHERE task_type = ? AND task_uuid = ?
              AND status IN ({placeholders})
            ORDER BY created_at
            LIMIT 1
            """,
            (task_type, task_uuid, *ACTIVE_INSTANCE_STATUSES),
        ).fetchone()

        if active_run:
            active = dict(active_run)
            result = {
                "reason": "instance_task_in_progress",
                "blocking_run_id": active["run_id"],
                "blocking_status": active["status"],
            }
            connection.execute(
                """
                INSERT INTO task_runs (
                    run_id, task_uuid, task_type, trigger_type, request_id,
                    status, parameters_json, result_json, created_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, 'skipped_busy', ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task_uuid,
                    task_type,
                    trigger_type,
                    request_id,
                    _json_dump(parameters),
                    _json_dump(result),
                    now,
                    now,
                ),
            )
            return run_id, active

        connection.execute(
            """
            INSERT INTO task_runs (
                run_id, task_uuid, task_type, trigger_type, request_id,
                status, parameters_json, created_at
            ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
            """,
            (
                run_id,
                task_uuid,
                task_type,
                trigger_type,
                request_id,
                _json_dump(parameters),
                now,
            ),
        )
    return run_id, None


def update_run(run_id, status, result=None, error=None):
    init_db()
    now = utc_now()
    fields = ["status = ?"]
    values = [status]

    if status == "running":
        fields.append("started_at = COALESCE(started_at, ?)")
        values.append(now)
    if status in (
        "submitted",
        "succeeded",
        "failed",
        "skipped_busy",
        "skipped_unavailable",
        "interrupted",
    ):
        fields.append("finished_at = ?")
        values.append(now)
    if result is not None:
        fields.append("result_json = ?")
        values.append(_json_dump(result))
    if error is not None:
        fields.append("error = ?")
        values.append(str(error)[:20000])

    values.append(run_id)
    with _connect() as connection:
        connection.execute(
            f"UPDATE task_runs SET {', '.join(fields)} WHERE run_id = ?",
            values,
        )


def record_alist_copy_tasks(run_id, tasks, source_dir, destination_dir, entry_name):
    if not tasks:
        return
    init_db()
    now = utc_now()
    with _connect() as connection:
        for task in tasks:
            connection.execute(
                """
                INSERT INTO alist_copy_tasks (
                    alist_task_id, run_id, source_dir, destination_dir, entry_name,
                    name, state, status, progress, start_time, end_time,
                    total_bytes, error, created_at, updated_at, last_seen_at,
                    completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alist_task_id) DO UPDATE SET
                    name = excluded.name,
                    state = excluded.state,
                    status = excluded.status,
                    progress = excluded.progress,
                    start_time = excluded.start_time,
                    end_time = excluded.end_time,
                    total_bytes = excluded.total_bytes,
                    error = excluded.error,
                    updated_at = excluded.updated_at,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    str(task["id"]),
                    run_id,
                    source_dir,
                    destination_dir,
                    entry_name,
                    task.get("name"),
                    task.get("state"),
                    task.get("status"),
                    task.get("progress"),
                    task.get("start_time"),
                    task.get("end_time"),
                    task.get("total_bytes"),
                    task.get("error"),
                    now,
                    now,
                    now,
                    None,
                ),
            )


def finalize_waiting_alist_runs(run_id=None):
    init_db()
    clauses = ["status = 'waiting_alist'"]
    values = []
    if run_id:
        clauses.append("run_id = ?")
        values.append(run_id)

    finalized = []
    with _connect() as connection:
        runs = connection.execute(
            f"SELECT run_id, result_json FROM task_runs WHERE {' AND '.join(clauses)}",
            values,
        ).fetchall()
        for run in runs:
            summary_row = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN completed_at IS NOT NULL THEN 1 ELSE 0 END) AS completed,
                       SUM(CASE WHEN completed_at IS NOT NULL AND state = 2 THEN 1 ELSE 0 END) AS succeeded,
                       SUM(CASE WHEN completed_at IS NOT NULL AND (state IS NULL OR state != 2) THEN 1 ELSE 0 END) AS failed,
                       AVG(progress) AS progress
                FROM alist_copy_tasks
                WHERE run_id = ?
                """,
                (run["run_id"],),
            ).fetchone()
            if not summary_row["total"]:
                continue

            summary = dict(summary_row)
            summary["completed"] = summary["completed"] or 0
            summary["succeeded"] = summary["succeeded"] or 0
            summary["failed"] = summary["failed"] or 0
            summary["pending"] = summary["total"] - summary["completed"]
            summary["progress"] = (
                round(summary["progress"], 2)
                if summary["progress"] is not None
                else None
            )
            try:
                result = json.loads(run["result_json"]) if run["result_json"] else {}
            except json.JSONDecodeError:
                result = {"previous_result": run["result_json"]}
            result["alist_task_summary"] = summary

            fields = ["result_json = ?"]
            update_values = [_json_dump(result)]
            if summary["pending"] == 0:
                final_status = "failed" if summary["failed"] else "succeeded"
                fields.extend(
                    [
                        "status = ?",
                        "finished_at = ?",
                        "postprocess_status = 'pending'",
                    ]
                )
                update_values.extend([final_status, utc_now()])
                if summary["failed"]:
                    failed_ids = connection.execute(
                        """
                        SELECT alist_task_id FROM alist_copy_tasks
                        WHERE run_id = ? AND completed_at IS NOT NULL
                          AND (state IS NULL OR state != 2)
                        ORDER BY created_at, alist_task_id
                        LIMIT 20
                        """,
                        (run["run_id"],),
                    ).fetchall()
                    fields.append("error = ?")
                    update_values.append(
                        "AList copy task failed or was canceled: "
                        + ", ".join(row["alist_task_id"] for row in failed_ids)
                    )
                finalized.append(
                    {
                        "run_id": run["run_id"],
                        "status": final_status,
                        "summary": summary,
                    }
                )
            update_values.append(run["run_id"])
            connection.execute(
                f"UPDATE task_runs SET {', '.join(fields)} WHERE run_id = ?",
                update_values,
            )
    return finalized


def _alist_task_map(tasks):
    return {
        str(task["id"]): task
        for task in tasks or []
        if isinstance(task, dict) and task.get("id")
    }


def reconcile_alist_copy_tasks(done_tasks, undone_tasks, missing_timeout_seconds=600):
    init_db()
    now = utc_now()
    done_by_id = _alist_task_map(done_tasks)
    undone_by_id = _alist_task_map(undone_tasks)
    stats = {
        "tracked_done": 0,
        "tracked_undone": 0,
        "missing_timed_out": 0,
    }
    with _connect() as connection:
        for task_id, task in done_by_id.items():
            cursor = connection.execute(
                """
                UPDATE alist_copy_tasks
                SET name = ?, state = ?, status = ?, progress = ?,
                    start_time = ?, end_time = ?, total_bytes = ?, error = ?,
                    updated_at = ?, last_seen_at = ?,
                    completed_at = COALESCE(completed_at, ?)
                WHERE alist_task_id = ?
                """,
                (
                    task.get("name"),
                    task.get("state"),
                    task.get("status"),
                    task.get("progress"),
                    task.get("start_time"),
                    task.get("end_time"),
                    task.get("total_bytes"),
                    task.get("error"),
                    now,
                    now,
                    now,
                    task_id,
                ),
            )
            stats["tracked_done"] += cursor.rowcount

        for task_id, task in undone_by_id.items():
            cursor = connection.execute(
                """
                UPDATE alist_copy_tasks
                SET name = ?, state = ?, status = ?, progress = ?,
                    start_time = ?, end_time = ?, total_bytes = ?, error = ?,
                    updated_at = ?, last_seen_at = ?
                WHERE alist_task_id = ? AND completed_at IS NULL
                """,
                (
                    task.get("name"),
                    task.get("state"),
                    task.get("status"),
                    task.get("progress"),
                    task.get("start_time"),
                    task.get("end_time"),
                    task.get("total_bytes"),
                    task.get("error"),
                    now,
                    now,
                    task_id,
                ),
            )
            stats["tracked_undone"] += cursor.rowcount

        timeout_seconds = max(0, int(missing_timeout_seconds or 0))
        if timeout_seconds:
            threshold = (
                datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
            ).isoformat(timespec="milliseconds")
            visible_ids = set(done_by_id) | set(undone_by_id)
            missing_rows = connection.execute(
                """
                SELECT child.alist_task_id
                FROM alist_copy_tasks AS child
                JOIN task_runs AS parent ON parent.run_id = child.run_id
                WHERE parent.status = 'waiting_alist'
                  AND child.completed_at IS NULL
                  AND child.last_seen_at <= ?
                """,
                (threshold,),
            ).fetchall()
            for row in missing_rows:
                task_id = row["alist_task_id"]
                if task_id in visible_ids:
                    continue
                cursor = connection.execute(
                    """
                    UPDATE alist_copy_tasks
                    SET state = 4,
                        status = 'missing_timeout',
                        error = ?,
                        updated_at = ?,
                        completed_at = ?
                    WHERE alist_task_id = ? AND completed_at IS NULL
                    """,
                    (
                        f"AList task was absent from both done and undone for {timeout_seconds} seconds",
                        now,
                        now,
                        task_id,
                    ),
                )
                stats["missing_timed_out"] += cursor.rowcount

    stats["finalized_runs"] = finalize_waiting_alist_runs()
    return stats


def claim_pending_postprocess_runs(limit=100):
    init_db()
    now = utc_now()
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT run_id, status
            FROM task_runs
            WHERE postprocess_status = 'pending'
            ORDER BY finished_at, created_at
            LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        if not rows:
            return []
        run_ids = [row["run_id"] for row in rows]
        placeholders = ",".join("?" for _ in run_ids)
        connection.execute(
            f"""
            UPDATE task_runs
            SET postprocess_status = 'running',
                postprocess_started_at = ?,
                postprocess_finished_at = NULL
            WHERE run_id IN ({placeholders}) AND postprocess_status = 'pending'
            """,
            (now, *run_ids),
        )
        tasks = connection.execute(
            f"""
            SELECT * FROM alist_copy_tasks
            WHERE run_id IN ({placeholders})
            ORDER BY created_at, alist_task_id
            """,
            run_ids,
        ).fetchall()

    tasks_by_run = {run_id: [] for run_id in run_ids}
    for task in tasks:
        tasks_by_run[task["run_id"]].append(dict(task))
    return [
        {
            "run_id": row["run_id"],
            "status": row["status"],
            "tasks": tasks_by_run[row["run_id"]],
        }
        for row in rows
    ]


def finish_run_postprocess(run_id, success, result):
    init_db()
    with _connect() as connection:
        row = connection.execute(
            "SELECT result_json FROM task_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if not row:
            return
        try:
            run_result = json.loads(row["result_json"]) if row["result_json"] else {}
        except json.JSONDecodeError:
            run_result = {"previous_result": row["result_json"]}
        run_result["postprocess"] = result
        connection.execute(
            """
            UPDATE task_runs
            SET result_json = ?, postprocess_status = ?, postprocess_finished_at = ?
            WHERE run_id = ?
            """,
            (
                _json_dump(run_result),
                "succeeded" if success else "failed",
                utc_now(),
                run_id,
            ),
        )


def record_callback(
    service,
    target,
    request_payload,
    success,
    response=None,
    status_code=None,
    duration_ms=None,
    error=None,
    run_id=None,
):
    init_db()
    callback_id = str(uuid.uuid4())
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO callback_attempts (
                callback_id, run_id, service, target, status, request_json,
                response_json, status_code, duration_ms, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                callback_id,
                run_id,
                service,
                sanitize_url(target),
                "succeeded" if success else "failed",
                _json_dump(request_payload),
                _json_dump(response),
                status_code,
                duration_ms,
                str(error)[:20000] if error else None,
                utc_now(),
            ),
        )
    return callback_id


def _parse_json_columns(row):
    item = dict(row)
    for key in ("parameters_json", "result_json", "payload_json", "response_json", "request_json"):
        if key in item:
            raw_value = item.pop(key)
            output_key = key.removesuffix("_json")
            if raw_value:
                try:
                    item[output_key] = json.loads(raw_value)
                except json.JSONDecodeError:
                    item[output_key] = raw_value
            else:
                item[output_key] = None
    return item


def list_runs(
    limit=100,
    status=None,
    task_type=None,
    task_uuid=None,
    trigger_type=None,
    created_from=None,
    created_to=None,
):
    init_db()
    clauses = []
    values = []
    for column, value in (
        ("status", status),
        ("task_type", task_type),
        ("task_uuid", task_uuid),
        ("trigger_type", trigger_type),
    ):
        if value:
            clauses.append(f"{column} = ?")
            values.append(value)
    if created_from:
        clauses.append("created_at >= ?")
        values.append(created_from)
    if created_to:
        clauses.append("created_at < ?")
        values.append(created_to)
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    values.append(max(1, min(int(limit), 500)))
    with _connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM task_runs {where_clause} ORDER BY created_at DESC LIMIT ?",
            values,
        ).fetchall()
        run_ids = [row["run_id"] for row in rows]
        summaries = {}
        if run_ids:
            placeholders = ",".join("?" for _ in run_ids)
            summary_rows = connection.execute(
                f"""
                SELECT run_id,
                       COUNT(*) AS total,
                       SUM(CASE WHEN completed_at IS NOT NULL AND state = 2 THEN 1 ELSE 0 END) AS succeeded,
                       SUM(CASE WHEN completed_at IS NOT NULL AND (state IS NULL OR state != 2) THEN 1 ELSE 0 END) AS failed,
                       AVG(progress) AS progress
                FROM alist_copy_tasks
                WHERE run_id IN ({placeholders})
                GROUP BY run_id
                """,
                run_ids,
            ).fetchall()
            for summary_row in summary_rows:
                summary = dict(summary_row)
                summary["pending"] = (
                    summary["total"] - summary["succeeded"] - summary["failed"]
                )
                summary["progress"] = (
                    round(summary["progress"], 2)
                    if summary["progress"] is not None
                    else None
                )
                summaries[summary["run_id"]] = {
                    key: value for key, value in summary.items() if key != "run_id"
                }

    runs = [_parse_json_columns(row) for row in rows]
    for run in runs:
        if run["run_id"] in summaries:
            run["alist_task_summary"] = summaries[run["run_id"]]
    return runs


def get_run(run_id, child_limit=500, child_offset=0):
    init_db()
    limit = max(1, min(int(child_limit), 1000))
    offset = max(0, int(child_offset))
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM task_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        task_rows = connection.execute(
            """
            SELECT * FROM alist_copy_tasks
            WHERE run_id = ?
            ORDER BY created_at, alist_task_id
            LIMIT ? OFFSET ?
            """,
            (run_id, limit, offset),
        ).fetchall()
        summary_row = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN completed_at IS NOT NULL AND state = 2 THEN 1 ELSE 0 END) AS succeeded,
                   SUM(CASE WHEN completed_at IS NOT NULL AND (state IS NULL OR state != 2) THEN 1 ELSE 0 END) AS failed,
                   AVG(progress) AS progress
            FROM alist_copy_tasks
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()

    run = _parse_json_columns(row)
    tasks = [dict(task_row) for task_row in task_rows]
    summary = dict(summary_row)
    summary["succeeded"] = summary["succeeded"] or 0
    summary["failed"] = summary["failed"] or 0
    summary["pending"] = summary["total"] - summary["succeeded"] - summary["failed"]
    summary["progress"] = (
        round(summary["progress"], 2) if summary["progress"] is not None else None
    )
    run["alist_tasks"] = tasks
    run["alist_tasks_page"] = {
        "offset": offset,
        "limit": limit,
        "returned": len(tasks),
        "total": summary["total"],
        "truncated": offset + len(tasks) < summary["total"],
    }
    if summary["total"]:
        run["alist_task_summary"] = summary
    return run


def list_api_requests(limit=100):
    init_db()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT r.*,
                   COUNT(t.run_id) AS run_count
            FROM api_requests r
            LEFT JOIN task_runs t ON t.request_id = r.request_id
            GROUP BY r.request_id
            ORDER BY r.received_at DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return [_parse_json_columns(row) for row in rows]


def list_callbacks(limit=100, status=None):
    init_db()
    where_clause = "WHERE status = ?" if status else ""
    values = [status] if status else []
    values.append(max(1, min(int(limit), 500)))
    with _connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM callback_attempts {where_clause} ORDER BY created_at DESC LIMIT ?",
            values,
        ).fetchall()
    return [_parse_json_columns(row) for row in rows]


def overview_counts():
    init_db()
    local_now = datetime.now().astimezone()
    today = local_now.replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc).isoformat()
    with _connect() as connection:
        run_rows = connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM task_runs
            WHERE created_at >= ?
            GROUP BY status
            """,
            (today,),
        ).fetchall()
        callback_failures = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM callback_attempts
            WHERE status = 'failed' AND created_at >= ?
            """,
            (today,),
        ).fetchone()["count"]
    return {
        "runs_today": {row["status"]: row["count"] for row in run_rows},
        "callback_failures_today": callback_failures,
    }


init_db()
