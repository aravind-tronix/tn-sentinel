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
