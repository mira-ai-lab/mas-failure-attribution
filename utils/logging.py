"""Central logging setup for file and console outputs."""

import logging
import os
from logging.handlers import RotatingFileHandler
from utils.config import config
logger = logging.getLogger()
logger.setLevel(config.log_level)
log_file = os.environ.get("MAS_FAIL_ATTR_LOG", "mas-fail-attr.log")
# mode='w': one clean log file per process; avoid startup doRollover leaving empty .log.1
handler = RotatingFileHandler(
    log_file,
    mode="w",
    maxBytes=50_000_000,
    backupCount=3,
    encoding="utf-8",
)
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

console = logging.StreamHandler()
console.setLevel(config.log_level)
console.setFormatter(formatter)
logger.addHandler(console)

# httpx logs every HTTP request at INFO; suppress unless WARNING+.
logging.getLogger("httpx").setLevel(logging.WARNING)
