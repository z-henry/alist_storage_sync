import json
import re

from flask import Flask, g, jsonify, request

import logger_config
import runtime_store
from config import Task
from task_manager import (
    check_dir_tree_build,
    check_tasks,
    dir_tree_build_tasks,
    infer_dst_path,
    start_checker,
    sync_tasks,
)
from ui_routes import ui_blueprint
from version import APP_VERSION


app = Flask(__name__)
app.register_blueprint(ui_blueprint)


TRACKED_API_ENDPOINTS = {
    "sync_now",
    "sync_from_common",
    "sync_from_aliyunsub",
    "sync_from_moviepilot",
    "run_dir_tree_build_now",
}

@app.before_request
def record_inbound_request():
    if request.endpoint not in TRACKED_API_ENDPOINTS:
        return None

    payload = request.get_json(silent=True)
    if payload is None and request.data:
        try:
            payload = json.loads(request.get_data(as_text=True))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {
                "non_json_body_omitted": True,
                "body_size": len(request.data),
            }
    route = request.url_rule.rule if request.url_rule else request.path
    task_uuid = (request.view_args or {}).get("id")
    g.api_request_id = runtime_store.create_api_request(
        route=route,
        method=request.method,
        payload=payload,
        task_uuid=task_uuid,
    )
    return None


@app.after_request
def finish_inbound_request(response):
    request_id = getattr(g, "api_request_id", None)
    if request_id:
        runtime_store.finish_api_request(
            request_id=request_id,
            status_code=response.status_code,
            response=response.get_json(silent=True),
        )
    return response


def _request_id():
    return getattr(g, "api_request_id", None)


@app.route("/sync", methods=["POST"])
def sync_now():
    try:
        data = request.get_json() or {}
        task = Task("manual-sync", data.get("src", ""), data.get("dst", ""))
        if not task.src:
            logger_config.logger.error("[sync_now] param src is required")
            return jsonify({"status": "fail", "message": "param src is required"}), 400

        if not task.dst:
            task.dst = infer_dst_path(task.src)
            if not task.dst:
                logger_config.logger.error("[sync_now] param dst is required")
                return jsonify({"status": "fail", "message": "param dst is required"}), 400
            logger_config.logger.info(f'[sync_now] inferred dst path "{task.dst}"')

        run_id = check_tasks(task, True, "api", _request_id())
        message = f"Sync initiated from {task.src} to {task.dst}"
        logger_config.logger.info(message)
        return jsonify(
            {
                "status": "success",
                "message": message,
                "request_id": _request_id(),
                "run_ids": [run_id],
            }
        ), 200
    except Exception as error:
        logger_config.logger.exception(f"[sync_now] An error occurred: {error}")
        return jsonify({"status": "fail", "message": f"An error occurred: {error}"}), 400


@app.route("/update/common/<id>", methods=["POST"])
def sync_from_common(id):
    try:
        data = request.get_json() or {}
        logger_config.logger.info(f"[sync_from_common] receive: {data}")

        task_matched = next((task for task in sync_tasks if task.uuid == id), None)
        if not task_matched:
            message = f"task:{id} not found"
            logger_config.logger.error(f"[sync_from_common] {message}")
            return jsonify({"status": "fail", "message": message}), 400

        task = Task(id, data.get("src", ""), data.get("dst", ""))
        if not task.src:
            message = f"task:{id} param src is required"
            logger_config.logger.error(f"[sync_from_common] {message}")
            return jsonify({"status": "fail", "message": message}), 400

        if not task.dst:
            if task_matched.src not in task.src:
                message = f'task:{id} src path does not match "{task_matched.src}"'
                logger_config.logger.error(f"[sync_from_common] {message}")
                return jsonify({"status": "fail", "message": message}), 400

            pattern = f"^{re.escape(task_matched.src)}"
            task.dst = re.sub(pattern, task_matched.dst, task.src)
            logger_config.logger.info(
                f'[sync_from_common] task:{id} inferred dst path "{task.dst}"'
            )

        run_id = check_tasks(task, True, "api", _request_id())
        message = f"Sync initiated task:{task.uuid} from {task.src} to {task.dst}"
        logger_config.logger.info(f"[sync_from_common] {message}")
        return jsonify(
            {
                "status": "success",
                "message": message,
                "request_id": _request_id(),
                "run_ids": [run_id],
            }
        ), 200
    except Exception as error:
        message = f"An error occurred: {error}"
        logger_config.logger.exception(f"[sync_from_common] {message}")
        return jsonify({"status": "fail", "message": message}), 400


@app.route("/update/aliyunsub/<id>", methods=["POST"])
def sync_from_aliyunsub(id):
    try:
        data = json.loads(request.get_data(as_text=True))
        if data.get("toFileName") == "<no value>":
            return jsonify({"status": "fail", "message": "not subscribe update"}), 400
        logger_config.logger.info(f"[sync_from_aliyunsub] receive: {data}")

        task_matched = next((task for task in sync_tasks if task.uuid == id), None)
        if not task_matched:
            message = f"task:{id} not found"
            logger_config.logger.error(f"[sync_from_aliyunsub] {message}")
            return jsonify({"status": "fail", "message": message}), 400

        relative_path = data.get("title") + "/" + data.get("toFileName")
        task = Task(
            id,
            task_matched.src + "/" + relative_path,
            task_matched.dst + "/" + relative_path,
        )
        run_id = check_tasks(task, True, "api", _request_id())
        message = f"Sync initiated task:{task.uuid} from {task.src} to {task.dst}"
        logger_config.logger.info(f"[sync_from_aliyunsub] {message}")
        return jsonify(
            {
                "status": "success",
                "message": message,
                "request_id": _request_id(),
                "run_ids": [run_id],
            }
        ), 200
    except Exception as error:
        message = f"An error occurred: {error}"
        logger_config.logger.exception(f"[sync_from_aliyunsub] {message}")
        return jsonify({"status": "fail", "message": message}), 400


@app.route("/update/movie-pilot/<id>", methods=["POST"])
def sync_from_moviepilot(id):
    try:
        data = request.get_json() or {}
        if data.get("type") != "transfer.complete":
            return jsonify({"status": "fail", "message": "not transfer.complete"}), 400
        transfer_info = (data.get("data") or {}).get("transferinfo") or {}
        if transfer_info.get("success") is False:
            return jsonify({"status": "fail", "message": "transferinfo not success"}), 400
        logger_config.logger.info(f"[sync_from_moviepilot] receive: {data}")

        task_matched = next((task for task in sync_tasks if task.uuid == id), None)
        if not task_matched:
            message = f"task:{id} not found"
            logger_config.logger.error(f"[sync_from_moviepilot] {message}")
            return jsonify({"status": "fail", "message": message}), 400

        run_ids = []
        pattern = f"^{re.escape(task_matched.mounted_path)}"
        for final_target_path in transfer_info.get("file_list_new") or []:
            task = Task(
                id,
                re.sub(pattern, task_matched.src, final_target_path),
                re.sub(pattern, task_matched.dst, final_target_path),
            )
            run_ids.append(check_tasks(task, True, "api", _request_id()))
            logger_config.logger.info(
                f"[sync_from_moviepilot] queued task:{id} from {task.src} to {task.dst}"
            )

        message = f"Sync initiated task:{id}, queued {len(run_ids)} file(s)"
        return jsonify(
            {
                "status": "success",
                "message": message,
                "request_id": _request_id(),
                "run_ids": run_ids,
            }
        ), 200
    except Exception as error:
        message = f"An error occurred: {error}"
        logger_config.logger.exception(f"[sync_from_moviepilot] {message}")
        return jsonify({"status": "fail", "message": message}), 400


@app.route("/dir_tree_build", methods=["GET"])
def run_dir_tree_build_now():
    try:
        run_ids = [
            check_dir_tree_build(task, "api", _request_id())
            for task in dir_tree_build_tasks
        ]
        message = f"Dir tree build initiated for {len(run_ids)} tasks"
        logger_config.logger.info(f"[dir_tree_build_now] {message}")
        return jsonify(
            {
                "status": "success",
                "message": message,
                "request_id": _request_id(),
                "run_ids": run_ids,
            }
        ), 200
    except Exception as error:
        message = f"An error occurred: {error}"
        logger_config.logger.exception(f"[dir_tree_build_now] {message}")
        return jsonify({"status": "fail", "message": message}), 400


if __name__ == "__main__":
    logger_config.logger.info(f"App version: {APP_VERSION}")
    logger_config.logger.info("Starting task checker...")
    start_checker()
    logger_config.logger.info("Task checker started")
    app.run(host="0.0.0.0", port=8115, threaded=True, use_reloader=False)
