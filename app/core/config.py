from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    database_url_admin: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 120
    redis_url: str = "redis://localhost:6379/0"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    fcm_server_key: str = ""
    openai_api_key: str = ""
    maps_api_key: str = ""
    cors_origins: str = "http://localhost:4200"
    aws_endpoint_url: str = ""
    aws_s3_public_endpoint: str = ""  # URLs presignadas para el navegador (ej. localhost:4566)
    aws_region: str = "us-east-1"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    s3_bucket_evidencias: str = "emergencias-evidencias"
    public_tenant_id: str = "22222222-0000-0000-0000-000000000000"


settings = Settings()
