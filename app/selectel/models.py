from dataclasses import dataclass


@dataclass(frozen=True)
class FloatingIP:
    id: str
    address: str
    subnet_id: str
