"""
API Key authentication (tiêu chí 6).

  • Client gửi header  X-API-Key: <key>
  • Key phải trùng biến môi trường AGENT_API_KEY
  • Thiếu hoặc sai key → HTTP 401 (không phải 403 hay 422)
"""
import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """Dependency cho FastAPI — dùng trong /ask: Depends(verify_api_key)."""
    expected = settings.agent_api_key
    # compare_digest: so sánh thời gian hằng, tránh timing attack.
    # Server chưa cấu hình key (expected rỗng) → từ chối hết, không "mở cửa" mặc định.
    if not api_key or not expected or not hmac.compare_digest(api_key, expected):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key (header X-API-Key)",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return api_key
