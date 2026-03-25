from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service
from app.database.connection import get_db

security = HTTPBearer()


async def get_current_user(
    crendential: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
):
    crendential_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = service.decode_token(crendential.credentials)
        if payload.get("type") != "access":
            raise crendential_exception
        user_id: str = payload.get("sub")
        if not user_id:
            raise crendential_exception
    except Exception:
        raise crendential_exception

    user = await service.get_user_by_email(db, user_id)
    if not user or not user.is_active:
        raise crendential_exception
    return user
