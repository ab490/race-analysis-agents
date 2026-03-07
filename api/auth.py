"""
API key authentication.

Set API_KEY in the environment to enable auth. If not set, all requests are allowed
(useful for local development). When enabled, every request must include:

    X-API-Key: <your key>
"""

import os

from fastapi import Header, HTTPException, status


async def require_api_key(x_api_key: str = Header(default="")) -> None:
    expected = os.getenv("API_KEY", "")
    if not expected:
        return  # Auth disabled — no API_KEY configured
    if x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Pass it as the X-API-Key header.",
        )
