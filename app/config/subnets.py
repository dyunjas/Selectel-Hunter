TARGET_SUBNETS = [
    {"region": "ru-1", "cidr": "46.182.24.0/24", "subnet_id": "a47cd3be-6c09-4a72-a852-780d9cc07937"},
    {"region": "ru-3", "cidr": "87.228.101.0/24", "subnet_id": "53b0d1b5-a8f8-40aa-9c16-b2a60468c2ca"},
    {"region": "ru-3", "cidr": "188.68.218.0/24", "subnet_id": "c2578c6f-b81b-48b2-aa41-fc7e3b0be10d"},
    {"region": "ru-3", "cidr": "185.91.54.0/24", "subnet_id": "86ae307b-7aa8-437e-88c7-1f5332048bb2"},
    {"region": "ru-3", "cidr": "185.91.53.0/24", "subnet_id": "95b0f7b1-401e-43ea-a695-390524a1d35d"},
    {"region": "ru-3", "cidr": "185.91.52.0/24", "subnet_id": "0f922f97-87f0-4d84-9278-f1a87dee936e"},
    {"region": "ru-3", "cidr": "37.9.4.0/24", "subnet_id": "f72a2a27-356e-4d84-8ed9-4c562bdf3449"},
    {"region": "ru-9", "cidr": "87.228.33.0/24", "subnet_id": "bce83190-c8d0-4d83-897d-7664ee3e9aeb"},
    {"region": "ru-9", "cidr": "87.228.32.0/24", "subnet_id": "d531e21f-963a-46df-a2fc-4966ab21912e"},
    {"region": "ru-9", "cidr": "31.129.42.0/24", "subnet_id": "23a85638-428b-44f0-a4e0-d2fbb48f0093"},
]

BY_ID = {item["subnet_id"]: item for item in TARGET_SUBNETS}
