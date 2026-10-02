"""X-Ray 定时任务包。

  * tasks/scheduler.py   —— APScheduler 装配（lifespan 中启动）
  * tasks/night_batch.py —— 夜间批处理：**只做数据采集并写入数据库**
                            （不计算信号、不预生成答案）

本文件存在的必要性：没有它 `tasks/night_batch.py` 无法被 `tasks.night_batch` 导入。
"""

from __future__ import annotations

__all__: list[str] = []
