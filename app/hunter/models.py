from dataclasses import dataclass


@dataclass(frozen=True)
class TaskKey:
    account_id: int
    subnet_id: str
