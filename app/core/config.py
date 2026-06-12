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
    # AcquireMock payment gateway (mock stripe)
    acquiremock_url: str = "http://acquiremock:8000"
    acquiremock_webhook_secret: str = "am-dev-secret-min-32-chars-!!change"
    # URLs para webhooks y redirects (configurar según entorno)
    backend_internal_url: str = "http://backend:8000"
    web_public_url: str = "http://localhost:4200"
    # Email provider: "console" (dev) | "ses" (AWS) | "resend"
    email_provider: str = "console"
    ses_sender_email: str = "noreply@auxilio.app"
    resend_sender_email: str = "app@fivoryu.duckdns.org"
    # Resend email
    resend_api_key: str = ""
    environment: str = "development"
    fcm_server_key: str = ""
    openai_api_key: str = ""
    maps_api_key: str = ""
    cors_origins: str = "http://localhost:4200"
    # Flutter web (Chrome/desktop) usa puertos dinámicos, p. ej. http://localhost:65360
    cors_origin_regex: str = r"http://(localhost|127\.0\.0\.1):\d+"
    aws_endpoint_url: str = ""
    aws_s3_public_endpoint: str = ""  # URLs presignadas para el navegador (ej. localhost:4566)
    aws_region: str = "us-east-1"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    s3_bucket_evidencias: str = "emergencias-evidencias"
    public_tenant_id: str = "22222222-0000-0000-0000-000000000000"


settings = Settings()
