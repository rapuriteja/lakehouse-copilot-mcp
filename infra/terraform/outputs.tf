output "resource_group" {
  value = azurerm_resource_group.this.name
}

output "storage_account" {
  description = "ADLS Gen2 account. Landing files go to the 'landing' container."
  value       = azurerm_storage_account.lake.name
}

output "lakehouse_storage_url" {
  description = "abfss URL to use as the Unity Catalog managed location."
  value       = "abfss://lakehouse@${azurerm_storage_account.lake.name}.dfs.core.windows.net/"
}

output "databricks_workspace_url" {
  value = "https://${azurerm_databricks_workspace.this.workspace_url}"
}

output "databricks_access_connector_id" {
  description = "Use this when creating the Unity Catalog storage credential."
  value       = azurerm_databricks_access_connector.this.id
}

output "key_vault_uri" {
  value = azurerm_key_vault.this.vault_uri
}

output "azure_search_endpoint" {
  value = "https://${azurerm_search_service.this.name}.search.windows.net"
}

output "azure_openai_endpoint" {
  description = "Only set when enable_azure_openai = true."
  value       = one(azurerm_cognitive_account.openai[*].endpoint)
}

output "application_insights_connection_string" {
  value     = azurerm_application_insights.this.connection_string
  sensitive = true
}

output "env_file_hint" {
  description = "Values for your .env. Keys are read from Key Vault, so none are printed."
  value       = <<-EOT
    LLM_PROVIDER=openrouter
    EMBEDDING_PROVIDER=openrouter
    RETRIEVER=azure
    QUERY_ENGINE=databricks
    KEY_VAULT_URI=${azurerm_key_vault.this.vault_uri}
    AZURE_SEARCH_ENDPOINT=https://${azurerm_search_service.this.name}.search.windows.net
    DATABRICKS_HOST=${azurerm_databricks_workspace.this.workspace_url}
  EOT
}

output "key_vault_id" {
  description = "Resource id for the Databricks Key Vault-backed secret scope."
  value       = azurerm_key_vault.this.id
}
