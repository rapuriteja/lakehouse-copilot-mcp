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

variable "openai_location" {
  description = "Region for Azure OpenAI. Pick one where your models are available."
  type        = string
  default     = "eastus"
}

variable "chat_model" {
  description = "Azure OpenAI chat model used by the copilot."
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
  description = "Azure OpenAI embedding model for the RAG index."
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
