import httpx
from core.config import settings

NANGO_BASE_URL = "https://api.nango.dev"

def get_nango_records(integration_id: str, connection_id: str, model: str) -> list:
    url = f"{NANGO_BASE_URL}/records"
    headers = {
        "Authorization": f"Bearer {settings.nango_secret_key}",
        "Connection-Id": connection_id,
        "Provider-Config-Key": integration_id,
    }
    params = {"model": model}
    response = httpx.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json().get("records", [])

def get_hubspot_contacts(connection_id: str) -> list:
    return get_nango_records("hubspot", connection_id, "Contact")

def get_hubspot_deals(connection_id: str) -> list:
    return get_nango_records("hubspot", connection_id, "Deal")