import os
import json
import logging
from typing import Optional, Dict
from fastapi import Header, HTTPException, Security, status
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

# Standard API key header
API_KEY_HEADER = APIKeyHeader(name="X-Store-API-Key", auto_error=False)


def get_configured_store_keys() -> Dict[str, str]:
    """
    Retrieves configured store API keys mapping from environment.
    Format of BHARATVOICE_STORE_API_KEYS:
    JSON string: '{"key-default-123": "DEFAULT", "key-mumbai-456": "STORE_MUMBAI"}'
    or comma-separated pairs: 'key-default-123=DEFAULT,key-mumbai-456=STORE_MUMBAI'
    """
    raw = os.environ.get("BHARATVOICE_STORE_API_KEYS", "").strip()
    if not raw:
        return {}

    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except Exception as e:
            logger.error("Failed to parse BHARATVOICE_STORE_API_KEYS JSON: %s", e)
            return {}

    mapping = {}
    for pair in raw.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            mapping[k.strip()] = v.strip()
    return mapping


def get_authenticated_store_context(
    x_store_api_key: Optional[str] = Security(API_KEY_HEADER),
) -> Optional[str]:
    """
    Authenticates the caller and establishes a verified store identity.
    - If BHARATVOICE_STORE_API_KEYS is configured in environment, callers MUST provide
      a valid 'X-Store-API-Key' header. Unauthenticated requests are rejected with 401.
    - Returns the verified store_id bound to the provided credential.
    - If no keys are configured in environment (local dev mode), returns None.
    """
    configured_keys = get_configured_store_keys()

    if configured_keys:
        if not x_store_api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required: Provide a valid 'X-Store-API-Key' header.",
                headers={"WWW-Authenticate": "ApiKey"},
            )
        resolved_store = configured_keys.get(x_store_api_key)
        if not resolved_store:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or unrecognized store API key.",
                headers={"WWW-Authenticate": "ApiKey"},
            )
        return resolved_store

    return None


def resolve_effective_store(
    auth_store: Optional[str],
    requested_store: Optional[str] = None,
) -> str:
    """
    Combines verified auth identity with requested store parameter:
    - If caller is authenticated as store A and requested store B, rejects with 403.
    - If caller is authenticated, uses the authenticated store.
    - If unauthenticated (local dev), uses requested_store or 'DEFAULT'.
    """
    if auth_store is not None:
        if requested_store and requested_store.strip() and requested_store.strip() != auth_store:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: Caller is authenticated for store '{auth_store}' and cannot access store '{requested_store.strip()}'.",
            )
        return auth_store

    return requested_store.strip() if requested_store and requested_store.strip() else "DEFAULT"

