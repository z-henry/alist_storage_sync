from flask import Flask, request, jsonify
from task_manager import start_checker, infer_dst_path, sync_tasks, check_tasks

from config import Task
import logger_config  # 导入日志配置
import os
import re
import json

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

        check_tasks(task, True)
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
        if not task_matched:
            msg = f'task:{id} not found'
            logger_config.logger.error(f"[sync_from_common] {msg}")
            return jsonify({"status": "fail", "message": msg}), 400
        
        task = Task(f"/aliyunsub/{id}", data.get('src', ''), data.get('dst', ''))
        if not task.src:
            msg = f'task:{id} param src is required'
            logger_config.logger.error(f"[sync_from_common] {msg}")
            return jsonify({"status": "fail", "message": msg}), 400
        
        if not task.dst:
            if task_matched.src not in task.src:
                msg = f'task:{id} src path do not match "{task.src}"'
                logger_config.logger.error(f"[sync_from_common] {msg}")
                return jsonify({"status": "fail", "message": msg}), 400
            
            escaped_path = re.escape(task_matched.src)
            pattern = f"^{escaped_path}"
            task.src = re.sub(pattern, task_matched.dst, task.src)
            logger_config.logger.info(f'[sync_from_common] task:{id} dst path is not specified, inferring to "{task.dst}"')

        check_tasks(task, True)
        msg = f'Sync initiated task:{task.uuid} from {task.src} to {task.dst}' 
        logger_config.logger.info(f"[sync_from_common] {msg}")
        return jsonify({"status": "success", "message": msg}), 200
    
    except Exception as e:
        msg = f'An error occurred: {e}'
        logger_config.logger.error(f"[sync_from_common] {msg}")
        return jsonify({"status": "fail", "message": msg}), 400

@app.route('/update/aliyunsub/<id>', methods=['POST'])
def sync_from_aliyunsub(id):
    try:
        raw_data = request.data
        decoded_data = raw_data.decode('utf-8')
        data = json.loads(decoded_data)
        
        if data.get('toFileName') == "<no value>":
            return jsonify({"status": "fail", "message": 'not subscribe update'}), 400
        logger_config.logger.info(f'[sync_from_aliyunsub] receive: {data}')
        
        task_matched = next((task for task in sync_tasks if task.uuid == id), None)
        if not task_matched:
            msg = f'task:{id} not found'
            logger_config.logger.error(f"[sync_from_aliyunsub] {msg}")
            return jsonify({"status": "fail", "message": msg}), 400
        
        task = Task(f"/aliyunsub/{id}", '', '')
        relative_path = data.get('title') + '/' + data.get('toFileName')
        
        task.src = task_matched.src + '/' + relative_path
        task.dst = task_matched.dst + '/' + relative_path

        msg = f'Sync initiated task:{task.uuid} from {task.src} to {task.dst}' 
        logger_config.logger.info(f"[sync_from_aliyunsub] {msg}")
        check_tasks(task, True)
        return jsonify({"status": "success", "message": msg}), 200
    
    except Exception as e:
        msg = f'An error occurred: {e}'
        logger_config.logger.error(f"[sync_from_aliyunsub] {msg}")
        return jsonify({"status": "fail", "message": msg}), 400

@app.route('/update/movie-pilot/<id>', methods=['POST'])
def sync_from_moviepilot(id):
    try:
        data  = request.get_json()
        if data.get('type') != "transfer.complete":
            return jsonify({"status": "fail", "message": 'not transfer.complete'}), 400
        if data.get('data').get('transferinfo').get('success') is False:
            return jsonify({"status": "fail", "message": 'transferinfo not success'}), 400
        logger_config.logger.info(f'[sync_from_moviepilot] receive: {data}')
        
        task_matched = next((task for task in sync_tasks if task.uuid == id), None)
        if not task_matched:
            msg = f'task:{id} not found'
            logger_config.logger.error(f"[sync_from_moviepilot] {msg}")
            return jsonify({"status": "fail", "message": msg}), 400
        
        for iter_file in data.get('data').get('transferinfo').get('file_list_new'):
            final_target_path = iter
            
            task = Task(f"/movie-pilot/{id}", '', '')
            # 将 task_matched.mounted_path 进行转义，防止其中的正则特殊字符产生影响
            escaped_path = re.escape(task_matched.mounted_path)
            pattern = f"^{escaped_path}"
            task.src = re.sub(pattern, task_matched.src, final_target_path)
            task.dst = re.sub(pattern, task_matched.dst, final_target_path)

            msg = f'Sync initiated task:{task.uuid} from {task.src} to {task.dst}' 
            logger_config.logger.info(f"[sync_from_moviepilot] {msg}")
            check_tasks(task, True)
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
