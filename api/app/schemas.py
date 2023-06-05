import uuid

from fastapi_users import schemas
from pydantic import BaseModel


class UserRead(schemas.BaseUser[uuid.UUID]):
    pass


class UserCreate(schemas.BaseUserCreate):
    pass


class UserUpdate(schemas.BaseUserUpdate):
    pass


class DeploymentBase(BaseModel):
    id: str
    # owner_id: str


class DeploymentCreate(DeploymentBase):
    pass


class Deployment(DeploymentBase):
    id: str
    # owner_id: str
    type: str
    status: str | None = None

    # Config se usa para proveer configuraciones a Pydantic
    class Config:
        orm_mode = True
