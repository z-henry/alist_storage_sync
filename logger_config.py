import logging
from logging.handlers import RotatingFileHandler

# 配置日志记录
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

log_handler = RotatingFileHandler('app.log', maxBytes=10000, backupCount=1)
log_handler.setFormatter(log_formatter)
log_handler.setLevel(logging.INFO)

# 获取logger实例并配置
logger = logging.getLogger()
logger.addHandler(log_handler)
logger.setLevel(logging.INFO)
