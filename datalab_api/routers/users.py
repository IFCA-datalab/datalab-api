from fastapi import APIRouter

from ..schemas import UserInfo
from ..security import UserDep

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
def read_current_user(user: UserDep) -> UserInfo:
    """Identity carried by the DataLab token."""
    return UserInfo.model_validate(user.model_dump())
