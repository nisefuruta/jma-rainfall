"""python -m jma_rainfall で jma-rainfall コマンドと同じものを実行する。"""

import sys

from jma_rainfall.cli import main

sys.exit(main())
