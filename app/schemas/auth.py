from pydantic import BaseModel, EmailStr
from uuid import UUID


class RegisterIn(BaseModel):
    nombre: str
    email: EmailStr
    telefono: str | None = None
    password: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str
    tenant_id: UUID | None = None


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    rol: str
    tenant_id: str | None
    usuario_id: str
    must_change_password: bool = False


class ChangePasswordIn(BaseModel):
    password_actual: str
    password_nueva: str


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str
