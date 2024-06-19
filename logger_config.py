import logging
from logging.handlers import RotatingFileHandler

# 配置日志记录
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# 文件处理器
file_handler = RotatingFileHandler('app.log', maxBytes=10000, backupCount=1)
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)

# 控制台处理器
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

# 获取logger实例并配置
logger = logging.getLogger()
logger.addHandler(file_handler)
logger.addHandler(console_handler)
logger.setLevel(logging.INFO)
