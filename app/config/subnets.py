TARGET_SUBNETS = [
    {"cidr": "87.228.101.0/24", "subnet_id": "53b0d1b5-a8f8-40aa-9c16-b2a60468c2ca"},
    {"cidr": "188.68.218.0/24", "subnet_id": "c2578c6f-b81b-48b2-aa41-fc7e3b0be10d"},
    {"cidr": "185.91.54.0/24", "subnet_id": "86ae307b-7aa8-437e-88c7-1f5332048bb2"},
    {"cidr": "185.91.53.0/24", "subnet_id": "95b0f7b1-401e-43ea-a695-390524a1d35d"},
    {"cidr": "185.91.52.0/24", "subnet_id": "0f922f97-87f0-4d84-9278-f1a87dee936e"},
    {"cidr": "37.9.4.0/24", "subnet_id": "f72a2a27-356e-4d84-8ed9-4c562bdf3449"},
]

BY_ID = {item["subnet_id"]: item for item in TARGET_SUBNETS}
