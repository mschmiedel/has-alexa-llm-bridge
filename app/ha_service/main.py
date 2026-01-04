import httpx

from const import HA_URL, HA_TOKEN


async def execute_ha_service(domain: str, service: str, entity_id: str):
    """Führt Aktion aus"""
    if not HA_URL or not HA_TOKEN: return False
    url = f"{HA_URL}/api/services/{domain}/{service}"
    headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
    payload = {"entity_id": entity_id}
    print(f"HA ACTION: {domain}.{service} -> {entity_id}")
    async with httpx.AsyncClient() as http_client:
        try:
            resp = await http_client.post(url, json=payload, headers=headers, timeout=5.0)
            return resp.status_code == 200
        except Exception: return False
