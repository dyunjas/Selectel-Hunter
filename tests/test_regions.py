from types import SimpleNamespace

from app.config.regions import REGION_ORDER, REGIONS
from app.config.subnets import TARGET_SUBNETS
from app.selectel.client import SelectelClient


def test_target_subnets_have_region_and_ru9_targets():
    assert all(item["region"] in REGIONS for item in TARGET_SUBNETS)
    assert [item["cidr"] for item in TARGET_SUBNETS if item["region"] == "ru-9"] == [
        "87.228.33.0/24", "87.228.32.0/24", "31.129.42.0/24"
    ]


def test_ru1_region_and_target_configuration():
    assert REGION_ORDER == ["ru-1", "ru-3", "ru-9"]
    assert REGIONS["ru-1"] == {
        "network_api_url": "https://ru-1.cloud.api.selcloud.ru/network/v2.0",
        "floating_network_id": "ab2264dd-bde8-4a97-b0da-5fea63191019",
    }
    target = next(item for item in TARGET_SUBNETS if item["region"] == "ru-1")
    assert target == {
        "region": "ru-1",
        "cidr": "46.182.24.0/24",
        "subnet_id": "a47cd3be-6c09-4a72-a852-780d9cc07937",
    }


def test_client_uses_region_endpoint_and_network():
    account = SimpleNamespace(region="ru-9", network_api_url="old-url")
    client = SelectelClient(
        account,
        "password",
        region="ru-9",
        network_api_url=REGIONS["ru-9"]["network_api_url"],
        floating_network_id=REGIONS["ru-9"]["floating_network_id"],
    )
    assert client.network_api_url.endswith("ru-9.cloud.api.selcloud.ru/network/v2.0")
    assert client.network_id == "f16140a4-e736-4655-9a1b-d7d2df6f969e"
