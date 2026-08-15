import hashlib
import hmac
import os
import secrets

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import api_alist
import config
import runtime_store
import task_manager
from version import APP_VERSION


ui_blueprint = Blueprint("ui", __name__)
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _credential_fingerprint(username, password):
    value = f"{username}\0{password}".encode("utf-8")
    secret_key = current_app.secret_key
    if isinstance(secret_key, str):
        secret_key = secret_key.encode("utf-8")
    return hmac.new(secret_key, value, hashlib.sha256).hexdigest()


def _is_safe_ui_target(value):
    return bool(value and value.startswith("/ui") and not value.startswith("//"))


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
def require_ui_session():
    if request.endpoint == "ui.login":
        return None

    expected_password = os.environ.get("UI_PASSWORD")
    if not expected_password:
        return redirect(url_for("ui.login"))

    expected_username = os.environ.get("UI_USERNAME", "admin")
    expected_fingerprint = _credential_fingerprint(
        expected_username,
        expected_password,
    )
    authenticated = secrets.compare_digest(
        session.get("ui_auth", ""),
        expected_fingerprint,
    )
    if not authenticated:
        session.clear()
        if request.path.startswith("/ui/api/"):
            return jsonify({"message": "Authentication required"}), 401
        next_target = request.full_path.rstrip("?")
        return redirect(url_for("ui.login", next=next_target))

    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    if request.method in _WRITE_METHODS:
        supplied_token = request.headers.get("X-CSRF-Token") or request.form.get(
            "_csrf_token", ""
        )
        if not secrets.compare_digest(
            supplied_token,
            session.get("csrf_token", ""),
        ):
            return jsonify({"message": "Invalid CSRF token"}), 403
    return None


@ui_blueprint.after_request
def disable_ui_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@ui_blueprint.route("/ui")
@ui_blueprint.route("/ui/")
def index():
    return render_template(
        "ui.html",
        app_version=APP_VERSION,
        csrf_token=session["csrf_token"],
    )


@ui_blueprint.route("/ui/login", methods=["GET", "POST"])
def login():
    expected_password = os.environ.get("UI_PASSWORD")
    expected_username = os.environ.get("UI_USERNAME", "admin")
    next_target = request.args.get("next") or request.form.get("next") or "/ui"
    if not _is_safe_ui_target(next_target):
        next_target = "/ui"

    if not expected_password:
        return (
            render_template(
                "login.html",
                app_version=APP_VERSION,
                username=expected_username,
                next_target=next_target,
                disabled=True,
                error="UI 未启用，请先设置 UI_PASSWORD。",
            ),
            503,
        )

    expected_fingerprint = _credential_fingerprint(
        expected_username,
        expected_password,
    )
    if request.method == "GET" and secrets.compare_digest(
        session.get("ui_auth", ""),
        expected_fingerprint,
    ):
        return redirect(next_target)

    error = None
    status_code = 200
    submitted_username = request.form.get("username", "")
    if request.method == "POST":
        submitted_password = request.form.get("password", "")
        username_valid = secrets.compare_digest(
            submitted_username,
            expected_username,
        )
        password_valid = secrets.compare_digest(
            submitted_password,
            expected_password,
        )
        if username_valid and password_valid:
            session.clear()
            session.permanent = True
            session["ui_auth"] = expected_fingerprint
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(next_target)
        error = "用户名或密码错误。"
        status_code = 401

    return (
        render_template(
            "login.html",
            app_version=APP_VERSION,
            username=submitted_username or expected_username,
            next_target=next_target,
            disabled=False,
            error=error,
        ),
        status_code,
    )


@ui_blueprint.route("/ui/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("ui.login"))


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
    instance_keys.extend(
        ("alist2strm", task.uuid) for task in config.alist2strm_tasks
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

    for task in config.alist2strm_tasks:
        job = jobs.get(f"alist2strm:{task.uuid}", {})
        items.append(
            {
                "task_uuid": task.uuid,
                "task_type": "alist2strm",
                "name": f"STRM 生成 {task.uuid}",
                "schedule": task.cron,
                "next_run_time": job.get("next_run_time"),
                "parameters": {
                    **task.parameters(),
                    "output_root": config.strm_output_root,
                },
                "last_run": latest.get(("alist2strm", task.uuid)),
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
    "/ui/api/tasks/alist2strm/<task_uuid>/run",
    methods=["POST"],
)
def run_alist2strm_task(task_uuid):
    task = next(
        (item for item in config.alist2strm_tasks if item.uuid == task_uuid),
        None,
    )
    if task is None:
        return jsonify({"message": f"Alist2Strm task not found: {task_uuid}"}), 404
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

    run_id = task_manager.check_alist2strm(task, trigger_type="manual")
    run = runtime_store.get_run(run_id, child_limit=1)
    return (
        jsonify(
            {
                "message": "Alist2Strm task triggered",
                "run_id": run_id,
                "run": run,
            }
        ),
        202,
    )


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
