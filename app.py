from flask import Flask, request, jsonify
from sync import perform_sync
from task_manager import start_task_checker
import logger_config  # 导入日志配置
from config import Task

app = Flask(__name__)

@app.route('/sync', methods=['POST'])
def sync_now():
    data = request.get_json()
    task = Task(data.get('src'), data.get('dst'))
    if task:
        perform_sync(task)
        logger_config.logger.info(f"Sync initiated from {task.src} to {task.dst}")
        return jsonify({"status": "success", "message": f"Sync initiated from {task.src} to {task.dst}"}), 200
    else:
        logger_config.logger.error("src/dst is required")
        return jsonify({"status": "error", "message": "src/dst is required"}), 400

if __name__ == "__main__":
    logger_config.logger.info("Starting task checker...")
    start_task_checker()
    logger_config.logger.info("Task checker started")
    app.run(host='0.0.0.0', port=8115)
