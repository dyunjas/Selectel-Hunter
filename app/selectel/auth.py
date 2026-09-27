"""Authentication-related helpers kept separate from the network client API."""

from .client import SelectelClient


async def authenticate(client: SelectelClient) -> str:
    return await client.authenticate()
