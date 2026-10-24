REGIONS = {
    "ru-1": {
        "network_api_url": "https://ru-1.cloud.api.selcloud.ru/network/v2.0",
        "floating_network_id": "ab2264dd-bde8-4a97-b0da-5fea63191019",
    },
    "ru-3": {
        "network_api_url": "https://ru-3.cloud.api.selcloud.ru/network/v2.0",
        "floating_network_id": "966826e6-d301-4bb5-aa13-77a324d15f0d",
    },
    "ru-9": {
        "network_api_url": "https://ru-9.cloud.api.selcloud.ru/network/v2.0",
        "floating_network_id": "f16140a4-e736-4655-9a1b-d7d2df6f969e",
    },
}

REGION_ORDER = ["ru-1", "ru-3", "ru-9"]
REGIONS["ru-2"] = {
    "network_api_url": "https://ru-2.cloud.api.selcloud.ru/network/v2.0",
    "floating_network_id": "8cc4ee6a-8dc9-4865-8877-ef5b5cdef020",
}
REGION_ORDER.append("ru-2")
SUPPORTED_REGIONS = tuple(REGION_ORDER)
