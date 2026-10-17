variable "project" {
  description = "Short project name used in resource names (lowercase letters and digits)."
  type        = string
  default     = "lhcopilot"

  validation {
    condition     = can(regex("^[a-z0-9]{3,12}$", var.project))
    error_message = "Use 3-12 lowercase letters or digits."
  }
}

variable "environment" {
  description = "Environment name, for example dev or prod."
  type        = string
  default     = "dev"
}

variable "location" {
  description = "Azure region for most resources. Central India keeps data in India."
  type        = string
  default     = "centralindia"
}

variable "openrouter_api_key" {
  description = "OpenRouter API key, stored in Key Vault. Pass it as TF_VAR_openrouter_api_key, never in a file. Leave unset to add the secret by hand."
  type        = string
  default     = null
  sensitive   = true
}

variable "azure_databricks_sp_object_id" {
  description = "Object id of the AzureDatabricks enterprise app in your tenant. Leave null to look it up."
  type        = string
  default     = null
}

variable "key_vault_reader_object_ids" {
  description = "Object ids of users or managed identities that may read Key Vault secrets."
  type        = list(string)
  default     = []
}

variable "enable_azure_openai" {
  description = "Also create Azure OpenAI (optional; the copilot uses OpenRouter by default)."
  type        = bool
  default     = false
}

variable "openai_location" {
  description = "Region for Azure OpenAI (only used when enable_azure_openai = true)."
  type        = string
  default     = "eastus"
}

variable "chat_model" {
  description = "Azure OpenAI chat model (only when enable_azure_openai = true)."
  type = object({
    name     = string
    version  = string
    sku      = string
    capacity = number
  })
  default = {
    name     = "gpt-4o-mini"
    version  = "2024-07-18"
    sku      = "GlobalStandard"
    capacity = 10 # thousands of tokens per minute
  }
}

variable "embedding_model" {
  description = "Azure OpenAI embedding model (only when enable_azure_openai = true)."
  type = object({
    name     = string
    version  = string
    sku      = string
    capacity = number
  })
  default = {
    name     = "text-embedding-3-small"
    version  = "1"
    sku      = "Standard"
    capacity = 10
  }
}

variable "search_sku" {
  description = "Azure AI Search tier. 'free' costs nothing (one per subscription)."
  type        = string
  default     = "free"
}

variable "log_retention_days" {
  description = "How long Log Analytics keeps logs."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags added to every resource."
  type        = map(string)
  default = {
    project    = "lakehouse-copilot"
    managed_by = "terraform"
  }
}
