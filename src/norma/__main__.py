"""允许以 `python -m norma` 方式启动，等价于 `norma` 控制台脚本。

委托给 `norma.cli.cli:main`，避免重复解析逻辑；保持单一入口。
"""

import sys

from norma.cli.cli import main

if __name__ == "__main__":
    sys.exit(main() or 0)
