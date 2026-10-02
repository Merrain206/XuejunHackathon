"""X-Ray 服务层。

包含答案服务（services/answer_service.py）：请求时实时组装 signals / charts / answer。

本文件存在的必要性：没有它 `xray-backend/services/` 不是包，
`from services.answer_service import ...` 在 ask.py 里会直接 ImportError。
"""

from __future__ import annotations

__all__: list[str] = []
