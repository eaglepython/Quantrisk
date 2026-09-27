variable "region" {
  type    = string
  default = "us-east-2"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "account_suffix" {
  description = "Short unique suffix for globally unique bucket names (e.g. last 6 digits of the account id)."
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "image_tag" {
  description = "Git SHA of the image to run (pushed by CI)."
  type        = string
}

variable "alert_email" {
  type = string
}
