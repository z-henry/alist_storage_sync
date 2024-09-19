from flask import Flask, request, jsonify
from sync import perform_sync
from task_manager import start_checker, infer_dst_path
from config import Task
import logger_config  # 导入日志配置

app = Flask(__name__)

# 计划报废
@app.route('/sync', methods=['POST'])
def sync_now():
    try:
        data = request.get_json()
        task = Task('-1', data.get('src', ''), data.get('dst', ''))
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


@app.route('/update/common/<id>', methods=['POST'])
def sync_from_common(id):
    try:
        data = request.get_json()
        logger_config.logger.info(f'[sync_from_common] receive: {data}')
        
        task_matched = next((task for task in sync_tasks if task.uuid == id), None)
        if not task_find:
            msg = f'task:{id} not found'
            logger_config.logger.error(f"[sync_from_common] {msg}")
            return jsonify({"status": "fail", "message": msg}), 400
        
        task = Task(f"/update/aliyunsub/{id}", data.get('src', ''), data.get('dst', ''))
        if not task.src:
            msg = f'task:{id} param src is required'
            logger_config.logger.error(f"[sync_from_common] {msg}")
            return jsonify({"status": "fail", "message": msg}), 400
        
        if not task.dst:
            if task.src not in path:
                msg = f'task:{id} src path do not match "{task.src}"'
                logger_config.logger.error(f"[sync_from_common] {msg}")
                return jsonify({"status": "fail", "message": msg}), 400
            
            task.dst = path.replace(task.src, task_matched.src)
            logger_config.logger.info(f'[sync_from_common] task:{id} dst path is not specified, inferring to "{task.dst}"')

        perform_sync(task, data.get('refresh', False))
        msg = f'Sync initiated task:{task.uuid} from {task.src} to {task.dst}' 
        logger_config.logger.info(f"[sync_from_common] {msg}")
        return jsonify({"status": "success", "message": msg}), 200
    
    except Exception as e:
        msg = f'An error occurred: {e}'
        logger_config.logger.error(f"[sync_from_common] {msg}")
        return jsonify({"status": "fail", "message": msg}), 400

@app.route('/update/movie-pilot/<id>/', methods=['POST'])
def sync_from_moviepilot(id):
    try:
        data = request.get_json()
        if data.get('type') != "transfer.complete":
            return jsonify({"status": "success", "message": ''}), 200
        if data.get('data').get('transferinfo').get('success') is False:
            return jsonify({"status": "success", "message": ''}), 200
        logger_config.logger.info(f'[sync_from_moviepilot] receive: {data}')
        
        task_matched = next((task for task in sync_tasks if task.uuid == id), None)
        if not task_find:
            msg = f'task:{id} not found'
            logger_config.logger.error(f"[sync_from_moviepilot] {msg}")
            return jsonify({"status": "fail", "message": msg}), 400
        
        path = data.get('data').get('transferinfo').get('path')
        target_path = data.get('data').get('transferinfo').get('target_path')
        file_name = os.path.basename(path)
        final_target_path = os.path.join(target_path, file_name)
        
        task = Task(f"/update/aliyunsub/{id}", data.get('src', ''), data.get('dst', ''))
        if not task.src:
            msg = f'task:{id} param src is required'
            logger_config.logger.error(f"[sync_from_moviepilot] {msg}")
            return jsonify({"status": "fail", "message": msg}), 400
        
        if not task.dst:
            if task.src not in path:
                msg = f'task:{id} src path do not match "{task.src}"'
                logger_config.logger.error(f"[sync_from_moviepilot] {msg}")
                return jsonify({"status": "fail", "message": msg}), 400
            
            task.dst = path.replace(task.src, task_matched.src)
            logger_config.logger.info(f'[sync_from_moviepilot] task:{id} dst path is not specified, inferring to "{task.dst}"')

        perform_sync(task, data.get('refresh', False))
        msg = f'Sync initiated task:{task.uuid} from {task.src} to {task.dst}' 
        logger_config.logger.info(f"[sync_from_moviepilot] {msg}")
        return jsonify({"status": "success", "message": msg}), 200
    
    except Exception as e:
        msg = f'An error occurred: {e}'
        logger_config.logger.error(f"[sync_from_moviepilot] {msg}")
        return jsonify({"status": "fail", "message": msg}), 400

if __name__ == "__main__":
    logger_config.logger.info("Starting task checker...")
    start_checker()
    logger_config.logger.info("Task checker started")
    app.run(host='0.0.0.0', port=8115, threaded=True, use_reloader=False)
