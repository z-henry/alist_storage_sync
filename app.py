from flask import Flask, request, jsonify
from sync import perform_sync
from task_manager import start_checker, infer_dst_path
from config import Task
import logger_config  # 导入日志配置

app = Flask(__name__)

@app.route('/sync', methods=['POST'])
def sync_now():
    try:
        data = request.get_json()
        task = Task(data.get('src', ''), data.get('dst', ''))
        if not task.src:
            logger_config.logger.error(f"[sync_now]param src is required")
            return jsonify({"status": "fail", "message": f"param src is required"}), 400
        
        if not task.dst:
            task.dst = infer_dst_path(task.src)
            if not task.dst:
                logger_config.logger.error(f"[sync_now]param dst is required")
                return jsonify({"status": "fail", "message": f"param dst is required"}), 400
            logger_config.logger.info(f'[sync_now]The dst path is not specified, inferring to "{task.dst}"')

        perform_sync(task, data.get('refresh', False))
        logger_config.logger.info(f'Sync initiated from {task.src} to {task.dst}')
        return jsonify({"status": "success", "message": f"Sync initiated from {task.src} to {task.dst}"}), 200
    except Exception as e:
        logger_config.logger.error(f"[sync_now]An error occurred: {e}")
        return jsonify({"status": "fail", "message": f"An error occurred: {e}"}), 400
        

if __name__ == "__main__":
    logger_config.logger.info("Starting task checker...")
    start_checker()
    logger_config.logger.info("Task checker started")
    app.run(host='0.0.0.0', port=8115, threaded=True, use_reloader=False)
