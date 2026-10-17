"""Read a secret from Azure Key Vault.

Uses DefaultAzureCredential: your `az login` on a laptop, a managed identity in Azure.
Imported lazily, so the azure packages are only needed when KEY_VAULT_URI is set.
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache
def read_secret(vault_uri: str, name: str) -> str | None:
    from azure.core.exceptions import ResourceNotFoundError
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    client = SecretClient(vault_url=vault_uri, credential=DefaultAzureCredential())
    try:
        return client.get_secret(name).value
    except ResourceNotFoundError:
        return None
