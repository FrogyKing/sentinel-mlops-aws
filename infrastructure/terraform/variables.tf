variable "region" {
  type    = string
  default = "us-east-1"
}
variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "An explicit twelve-digit AWS account ID is required."
  }
}
variable "image_uri" {
  type = string
  validation {
    condition     = strcontains(var.image_uri, "@sha256:")
    error_message = "Use an immutable ECR image digest URI."
  }
}
variable "model_key" {
  type    = string
  default = "models/model.joblib"
}
variable "model_sha256" {
  type = string
  validation {
    condition     = can(regex("^[a-f0-9]{64}$", var.model_sha256))
    error_message = "Provide the trusted model SHA256 checksum."
  }
}
variable "budget_email" {
  type = string
  validation {
    condition     = can(regex("^[^@ ]+@[^@ ]+\\.[^@ ]+$", var.budget_email))
    error_message = "Provide a budget alert email."
  }
}
variable "stable_version" {
  type     = string
  default  = null
  nullable = true
}
variable "canary_version" {
  type     = string
  default  = null
  nullable = true
}
variable "canary_weight" {
  type    = number
  default = 0.05
  validation {
    condition     = var.canary_weight > 0 && var.canary_weight <= 0.1
    error_message = "Canary weight must be positive and at most ten percent."
  }
}
