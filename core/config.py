from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Database
    database_url: str

    # AI
    ai_api_key: str
    ai_base_url: str = "https://aicredits.in/v1"
    ai_model: str = "gpt-4o"
    ai_model_fast: str = "gpt-4o-mini"   # cheaper model for simple content

    # App
    app_name: str = "EarlyEcho"
    debug: bool = False

    nango_secret_key: str = ""

    # Slack
    slack_bot_token: str = ""

    # Email (Mailgun inbound)
    mailgun_signing_key: str = ""
    mailgun_domain: str = ""     # e.g. "inbound.earlyecho.com"

    class Config:
        env_file = ".env"

settings = Settings()

# This is your central config. Everything the app needs is pulled from .env through here - no credentials hardcoded anywhere.