# Lakehouse Copilot on Azure:
#   ADLS Gen2 (landing + lakehouse) -> Azure Databricks with Unity Catalog (access connector)
#   Azure AI Search (RAG index) + Azure OpenAI (chat + embeddings)
#   Key Vault for secrets, Log Analytics + Application Insights for monitoring

data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

locals {
  name = "${var.project}-${var.environment}"
  flat = "${var.project}${var.environment}${random_string.suffix.result}" # for names without dashes
  tags = merge(var.tags, { environment = var.environment })
  secret_values = {
    "azure-search-api-key" = azurerm_search_service.this.primary_key
    "azure-openai-api-key" = azurerm_cognitive_account.openai.primary_access_key
  }
}

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

# ------------------------------------------------------------------ monitoring

resource "azurerm_log_analytics_workspace" "this" {
  name                = "log-${local.name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.tags
}

resource "azurerm_application_insights" "this" {
  name                = "appi-${local.name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  workspace_id        = azurerm_log_analytics_workspace.this.id
  application_type    = "web"
  tags                = local.tags
}

# ------------------------------------------------------------------ storage (ADLS Gen2)

resource "azurerm_storage_account" "lake" {
  name                            = substr("st${local.flat}", 0, 24)
  location                        = azurerm_resource_group.this.location
  resource_group_name             = azurerm_resource_group.this.name
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  is_hns_enabled                  = true # hierarchical namespace = ADLS Gen2
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = false # Entra ID (RBAC) only, no account keys
  allow_nested_items_to_be_public = false
  tags                            = local.tags
}

resource "azurerm_storage_container" "this" {
  for_each              = toset(["landing", "lakehouse"])
  name                  = each.key
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

# ------------------------------------------------------------------ Databricks + Unity Catalog

resource "azurerm_databricks_workspace" "this" {
  name                        = "dbw-${local.name}"
  location                    = azurerm_resource_group.this.location
  resource_group_name         = azurerm_resource_group.this.name
  sku                         = "premium" # Unity Catalog needs Premium
  managed_resource_group_name = "rg-${local.name}-dbw-managed"
  tags                        = local.tags
}

# Managed identity that Unity Catalog uses to read and write the lake.
resource "azurerm_databricks_access_connector" "this" {
  name                = "dbac-${local.name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_role_assignment" "uc_storage" {
  scope                = azurerm_storage_account.lake.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_databricks_access_connector.this.identity[0].principal_id
}

resource "azurerm_monitor_diagnostic_setting" "databricks" {
  name                       = "diag-databricks"
  target_resource_id         = azurerm_databricks_workspace.this.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category = "jobs"
  }
  enabled_log {
    category = "unityCatalog"
  }
}

# ------------------------------------------------------------------ Azure AI Search (RAG)

resource "azurerm_search_service" "this" {
  name                = "srch-${local.name}-${random_string.suffix.result}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = var.search_sku
  semantic_search_sku = "free" # semantic ranker, free monthly quota
  tags                = local.tags
}

# ------------------------------------------------------------------ Azure OpenAI

resource "azurerm_cognitive_account" "openai" {
  name                  = "oai-${local.name}-${random_string.suffix.result}"
  location              = var.openai_location
  resource_group_name   = azurerm_resource_group.this.name
  kind                  = "OpenAI"
  sku_name              = "S0"
  custom_subdomain_name = "oai-${local.flat}"
  tags                  = local.tags
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = var.chat_model.name
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = var.chat_model.name
    version = var.chat_model.version
  }

  sku {
    name     = var.chat_model.sku
    capacity = var.chat_model.capacity
  }
}

resource "azurerm_cognitive_deployment" "embedding" {
  name                 = var.embedding_model.name
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = var.embedding_model.name
    version = var.embedding_model.version
  }

  sku {
    name     = var.embedding_model.sku
    capacity = var.embedding_model.capacity
  }
}

resource "azurerm_monitor_diagnostic_setting" "openai" {
  name                       = "diag-openai"
  target_resource_id         = azurerm_cognitive_account.openai.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category_group = "allLogs"
  }
  enabled_metric {
    category = "AllMetrics"
  }
}

# ------------------------------------------------------------------ Key Vault (secrets)

resource "azurerm_key_vault" "this" {
  name                       = substr("kv-${local.flat}", 0, 24)
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true # access by Azure RBAC roles, not access policies
  soft_delete_retention_days = 7
  purge_protection_enabled   = false # dev setting; turn on for prod
  tags                       = local.tags
}

# Whoever runs Terraform can write secrets.
resource "azurerm_role_assignment" "kv_deployer" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Service keys go straight into Key Vault; they never appear in code or outputs.
resource "azurerm_key_vault_secret" "this" {
  for_each     = toset(["azure-search-api-key", "azure-openai-api-key"])
  name         = each.key
  value        = local.secret_values[each.key]
  key_vault_id = azurerm_key_vault.this.id
  depends_on   = [azurerm_role_assignment.kv_deployer]
}

resource "azurerm_monitor_diagnostic_setting" "key_vault" {
  name                       = "diag-keyvault"
  target_resource_id         = azurerm_key_vault.this.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category = "AuditEvent"
  }
}
