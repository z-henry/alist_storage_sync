import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit


_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("APP_DB_PATH", os.path.join(_PROJECT_DIR, "data", "runtime.db"))
_SCHEMA_LOCK = threading.Lock()
_INITIALIZED = False
_SENSITIVE_KEYS = ("password", "passwd", "secret", "token", "apikey", "api_key", "authorization")


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
                    FOREIGN KEY (run_id) REFERENCES task_runs(run_id)
                )
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


def update_run(run_id, status, result=None, error=None):
    init_db()
    now = utc_now()
    fields = ["status = ?"]
    values = [status]

    if status == "running":
        fields.append("started_at = COALESCE(started_at, ?)")
        values.append(now)
    if status in ("submitted", "succeeded", "failed", "skipped_busy", "interrupted"):
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
                    total_bytes, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alist_task_id) DO UPDATE SET
                    name = excluded.name,
                    state = excluded.state,
                    status = excluded.status,
                    progress = excluded.progress,
                    start_time = excluded.start_time,
                    end_time = excluded.end_time,
                    total_bytes = excluded.total_bytes,
                    error = excluded.error,
                    updated_at = excluded.updated_at
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
                ),
            )


def finalize_waiting_alist_runs(run_id=None):
    init_db()
    clauses = ["status = 'waiting_alist'"]
    values = []
    if run_id:
        clauses.append("run_id = ?")
        values.append(run_id)

    with _connect() as connection:
        runs = connection.execute(
            f"SELECT run_id, result_json FROM task_runs WHERE {' AND '.join(clauses)}",
            values,
        ).fetchall()
        for run in runs:
            summary_row = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN state = 2 THEN 1 ELSE 0 END) AS succeeded,
                       SUM(CASE WHEN state IN (4, 7) THEN 1 ELSE 0 END) AS failed,
                       AVG(progress) AS progress
                FROM alist_copy_tasks
                WHERE run_id = ?
                """,
                (run["run_id"],),
            ).fetchone()
            if not summary_row["total"]:
                continue

            summary = dict(summary_row)
            summary["succeeded"] = summary["succeeded"] or 0
            summary["failed"] = summary["failed"] or 0
            summary["pending"] = (
                summary["total"] - summary["succeeded"] - summary["failed"]
            )
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
                fields.extend(["status = ?", "finished_at = ?"])
                update_values.extend([final_status, utc_now()])
                if summary["failed"]:
                    failed_ids = connection.execute(
                        """
                        SELECT alist_task_id FROM alist_copy_tasks
                        WHERE run_id = ? AND state IN (4, 7)
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
            update_values.append(run["run_id"])
            connection.execute(
                f"UPDATE task_runs SET {', '.join(fields)} WHERE run_id = ?",
                update_values,
            )


def reconcile_alist_copy_tasks(tasks):
    init_db()
    now = utc_now()
    with _connect() as connection:
        for task in tasks or []:
            task_id = task.get("id") if isinstance(task, dict) else None
            if not task_id:
                continue
            connection.execute(
                """
                UPDATE alist_copy_tasks
                SET name = ?, state = ?, status = ?, progress = ?,
                    start_time = ?, end_time = ?, total_bytes = ?, error = ?, updated_at = ?
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
                    str(task_id),
                ),
            )
    finalize_waiting_alist_runs()


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
                       SUM(CASE WHEN state = 2 THEN 1 ELSE 0 END) AS succeeded,
                       SUM(CASE WHEN state IN (4, 7) THEN 1 ELSE 0 END) AS failed,
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
                   SUM(CASE WHEN state = 2 THEN 1 ELSE 0 END) AS succeeded,
                   SUM(CASE WHEN state IN (4, 7) THEN 1 ELSE 0 END) AS failed,
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
