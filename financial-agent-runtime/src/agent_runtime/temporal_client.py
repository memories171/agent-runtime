from temporalio.client import Client

from agent_runtime.config import settings


async def connect_temporal() -> Client:
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
