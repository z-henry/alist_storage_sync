import os
import secrets

from flask import Blueprint, Response, jsonify, render_template, request

import api_alist
import config
import runtime_store
import task_manager
from version import APP_VERSION


ui_blueprint = Blueprint("ui", __name__)


def _limit(default=100):
    try:
        return max(1, min(int(request.args.get("limit", default)), 500))
    except (TypeError, ValueError):
        return default


def _offset():
    try:
        return max(0, int(request.args.get("offset", 0)))
    except (TypeError, ValueError):
        return 0


@ui_blueprint.before_request
def require_basic_auth():
    expected_password = os.environ.get("UI_PASSWORD")
    if not expected_password:
        return Response(
            "UI is disabled. Set UI_PASSWORD to enable it.",
            503,
            {"Content-Type": "text/plain; charset=utf-8"},
        )

    expected_username = os.environ.get("UI_USERNAME", "admin")
    auth = request.authorization
    valid = bool(
        auth
        and secrets.compare_digest(auth.username or "", expected_username)
        and secrets.compare_digest(auth.password or "", expected_password)
    )
    if valid:
        return None
    return Response(
        "Authentication required",
        401,
        {"WWW-Authenticate": 'Basic realm="alist_storage_sync UI"'},
    )


@ui_blueprint.after_request
def disable_ui_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@ui_blueprint.route("/ui")
@ui_blueprint.route("/ui/")
def index():
    return render_template("ui.html", app_version=APP_VERSION)


@ui_blueprint.route("/ui/api/overview")
def overview():
    runtime = task_manager.runtime_snapshot()
    counts = runtime_store.overview_counts()
    recent_runs = []
    instance_keys = [("sync", task.uuid) for task in config.sync_tasks]
    instance_keys.append(("cache_refresh", "cache-refresh"))
    instance_keys.extend(
        ("dir_tree_build", task.uuid) for task in config.dir_tree_build_tasks
    )
    instance_keys = list(dict.fromkeys(instance_keys))
    known_instances = set(instance_keys)
    for task_type, task_uuid in instance_keys:
        recent_runs.extend(
            runtime_store.list_runs(
                limit=10,
                task_type=task_type,
                task_uuid=task_uuid,
            )
        )

    # Include ad-hoc/API instances such as manual-sync even when they are not
    # declared in config.json.
    extra_counts = {}
    for run in runtime_store.list_runs(limit=100):
        key = (run["task_type"], run["task_uuid"])
        if key in known_instances or extra_counts.get(key, 0) >= 10:
            continue
        recent_runs.append(run)
        extra_counts[key] = extra_counts.get(key, 0) + 1
    recent_runs.sort(key=lambda run: run["created_at"], reverse=True)
    return jsonify(
        {
            "version": APP_VERSION,
            "runtime": runtime,
            "counts": counts,
            "recent_runs": recent_runs,
        }
    )


@ui_blueprint.route("/ui/api/tasks")
def tasks():
    jobs = {job["id"]: job for job in task_manager.scheduler_jobs()}
    recent_runs = runtime_store.list_runs(limit=500)
    latest = {}
    for run in recent_runs:
        latest.setdefault((run["task_type"], run["task_uuid"]), run)

    items = []
    for task in config.sync_tasks:
        job = jobs.get(f"sync:{task.uuid}", {})
        items.append(
            {
                "task_uuid": task.uuid,
                "task_type": "sync",
                "name": f"同步任务 {task.uuid}",
                "schedule": task.cron,
                "next_run_time": job.get("next_run_time"),
                "parameters": {
                    "src": task.src,
                    "dst": task.dst,
                    "mounted_path": task.mounted_path,
                },
                "last_run": latest.get(("sync", task.uuid)),
            }
        )

    cache_job = jobs.get("system:cache-refresh", {})
    items.append(
        {
            "task_uuid": "cache-refresh",
            "task_type": "cache_refresh",
            "name": "子任务巡检与父任务后处理",
            "schedule": "* * * * *",
            "next_run_time": cache_job.get("next_run_time"),
            "parameters": {
                "healthcheck_interval_seconds": config.alist_healthcheck_interval_seconds,
                "task_missing_timeout_seconds": config.alist_task_missing_timeout_seconds,
            },
            "last_run": latest.get(("cache_refresh", "cache-refresh")),
        }
    )

    for task in config.dir_tree_build_tasks:
        job = jobs.get(f"dir-tree:{task.uuid}", {})
        items.append(
            {
                "task_uuid": task.uuid,
                "task_type": "dir_tree_build",
                "name": f"目录树刷新 {task.uuid}",
                "schedule": task.cron,
                "next_run_time": job.get("next_run_time"),
                "parameters": {
                    "src": task.src,
                    "qps": task.qps,
                    "run_at_start": task.run_at_start,
                },
                "last_run": latest.get(("dir_tree_build", task.uuid)),
            }
        )
    return jsonify({"tasks": items})


@ui_blueprint.route(
    "/ui/api/tasks/dir-tree-build/<task_uuid>/run",
    methods=["POST"],
)
def run_dir_tree_build_task(task_uuid):
    task = next(
        (item for item in config.dir_tree_build_tasks if item.uuid == task_uuid),
        None,
    )
    if task is None:
        return jsonify({"message": f"Dir tree build task not found: {task_uuid}"}), 404
    if not api_alist.is_online():
        return (
            jsonify(
                {
                    "message": "AList is unavailable; task execution is paused",
                    "alist": api_alist.health_snapshot(),
                }
            ),
            503,
        )

    run_id = task_manager.check_dir_tree_build(task, trigger_type="manual")
    run = runtime_store.get_run(run_id, child_limit=1)
    return (
        jsonify(
            {
                "message": "Dir tree build task triggered",
                "run_id": run_id,
                "run": run,
            }
        ),
        202,
    )


@ui_blueprint.route("/ui/api/runs")
def runs():
    return jsonify(
        {
            "runs": runtime_store.list_runs(
                limit=_limit(),
                status=request.args.get("status"),
                task_type=request.args.get("task_type"),
                task_uuid=request.args.get("task_uuid"),
                trigger_type=request.args.get("trigger_type"),
                created_from=request.args.get("created_from"),
                created_to=request.args.get("created_to"),
            )
        }
    )


@ui_blueprint.route("/ui/api/runs/<run_id>")
def run_detail(run_id):
    run = runtime_store.get_run(
        run_id,
        child_limit=_limit(default=100),
        child_offset=_offset(),
    )
    if run is None:
        return jsonify({"message": "run not found"}), 404
    return jsonify({"run": run})


@ui_blueprint.route("/ui/api/requests")
def api_requests():
    return jsonify({"requests": runtime_store.list_api_requests(limit=_limit())})


@ui_blueprint.route("/ui/api/callbacks")
def callbacks():
    return jsonify(
        {
            "callbacks": runtime_store.list_callbacks(
                limit=_limit(), status=request.args.get("status")
            )
        }
    )


@ui_blueprint.route("/ui/api/config", methods=["GET"])
def read_config():
    return jsonify(
        {
            "config": config.get_config(),
            "path": config.CONFIG_PATH,
            "writable": os.access(config.CONFIG_PATH, os.W_OK),
        }
    )


@ui_blueprint.route("/ui/api/config", methods=["PUT"])
def write_config():
    if not request.is_json:
        return jsonify({"message": "Content-Type must be application/json"}), 415
    payload = request.get_json(silent=True)
    try:
        validated = config.validate_config(payload)
        task_manager.validate_scheduler_config(validated)
        saved = config.save_config(validated)
        health = task_manager.reload_scheduler()
    except (config.ConfigError, ValueError, KeyError) as error:
        return jsonify({"message": str(error)}), 400
    except OSError as error:
        return jsonify({"message": f"Failed to write config: {error}"}), 500
    return jsonify(
        {
            "message": "Configuration saved and applied",
            "config": saved,
            "alist": health,
        }
    )


@ui_blueprint.route("/ui/api/alist/recheck", methods=["POST"])
def recheck_alist():
    return jsonify({"alist": task_manager.refresh_alist_health()})
