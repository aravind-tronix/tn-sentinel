variable "aws_region" {
  description = "AWS region"
  default     = "ap-south-1" # Mumbai — closest to Tamil Nadu
}

variable "project" {
  description = "Project name used for all resource naming"
  default     = "tn-sentinel"
}

variable "environment" {
  description = "Deployment environment"
  default     = "prod"
}

variable "github_token" {
  description = "GitHub personal access token for Amplify → repo connection"
  type        = string
  sensitive   = true
}

variable "ui_domain" {
  description = "Amplify app default domain (breaks circular dep between API GW and Amplify)"
  default     = "d2l0lone63w0d6.amplifyapp.com"
}
