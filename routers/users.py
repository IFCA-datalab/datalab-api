from fastapi import APIRouter, Depends, HTTPException
from fastapi_users import exceptions, models, schemas
from fastapi_users.manager import BaseUserManager


router = APIRouter(
    prefix="/users",
)

# def get_user_deployments(
#         user_id:str, 
#         user_manager: BaseUserManager[models.UP, models.ID] = Depends(get_user_manager)):
#     # List users from DB
#     return "Usuarios"

@router.get("/")
def get_users():
    return "Usuarios"

@router.get("/{user_id}")
async def get_current_user(user_id: str):
    '''
    List the deployments of an user.

    params: user_id
    '''
    return user_id.get_deployments
