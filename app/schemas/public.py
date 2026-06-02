from pydantic import BaseModel, EmailStr, Field


class PlanOut(BaseModel):
    id: str
    nombre: str
    max_talleres: int
    max_tecnicos: int
    ia_avanzada: bool
    precio_mensual: float
    moneda: str = "BOB"


class TenantSignupIn(BaseModel):
    nombre_organizacion: str = Field(min_length=2, max_length=120)
    dominio: str | None = Field(default=None, max_length=120)
    plan_id: str
    admin_nombre: str = Field(min_length=2, max_length=120)
    admin_email: EmailStr
    admin_telefono: str | None = None
    password: str = Field(min_length=8, max_length=128)
