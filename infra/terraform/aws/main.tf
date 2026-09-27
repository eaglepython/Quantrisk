# QuantRisk on AWS: scheduled Fargate batch, RDS PostgreSQL, S3, CloudWatch + SNS alerting.
#
#   EventBridge Scheduler (weekdays 17:30 America/Chicago)
#        -> ECS Fargate task (image from ECR, `quantrisk run --as-of today`)
#        -> RDS PostgreSQL (results) + S3 (raw landing, curated Parquet, reports)
#        -> CloudWatch Logs (JSON, run_id on every line) -> metric filters -> alarms -> SNS email

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "quantrisk", environment = var.environment, managed_by = "terraform" }
  }
}

locals {
  name = "quantrisk-${var.environment}"
}

# ------------------------------------------------------------------ storage
resource "aws_s3_bucket" "data" {
  bucket = "${local.name}-data-${var.account_suffix}"
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "aws:kms" }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    id     = "raw-to-glacier"
    status = "Enabled"
    filter { prefix = "raw/" }
    transition {
      days          = 90
      storage_class = "GLACIER_IR"
    }
  }
}

resource "aws_ecr_repository" "app" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
}

# ------------------------------------------------------------------ database
resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "db" {
  name       = local.name
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "db" {
  name   = "${local.name}-db"
  vpc_id = var.vpc_id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.task.id]
  }
}

resource "aws_security_group" "task" {
  name   = "${local.name}-task"
  vpc_id = var.vpc_id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_instance" "pg" {
  identifier                   = local.name
  engine                       = "postgres"
  engine_version               = "16"
  instance_class               = var.db_instance_class
  allocated_storage            = 50
  storage_encrypted            = true
  db_name                      = "quantrisk"
  username                     = "quantrisk"
  password                     = random_password.db.result
  db_subnet_group_name         = aws_db_subnet_group.db.name
  vpc_security_group_ids       = [aws_security_group.db.id]
  backup_retention_period      = 14
  deletion_protection          = var.environment == "prod"
  skip_final_snapshot          = var.environment != "prod"
  performance_insights_enabled = true
}

resource "aws_secretsmanager_secret" "db_url" {
  name = "${local.name}/database-url"
}

resource "aws_secretsmanager_secret_version" "db_url" {
  secret_id     = aws_secretsmanager_secret.db_url.id
  secret_string = "postgresql://quantrisk:${random_password.db.result}@${aws_db_instance.pg.address}:5432/quantrisk"
}

# ------------------------------------------------------------------ IAM
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_secretsmanager_secret.db_url.arn]
    }]
  })
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "task_s3" {
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
      Resource = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]
    }]
  })
}

# ------------------------------------------------------------------ compute
resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "batch" {
  name              = "/quantrisk/${var.environment}/batch"
  retention_in_days = 400
}

resource "aws_ecs_task_definition" "batch" {
  family                   = "${local.name}-batch"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 2048
  memory                   = 8192
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions = jsonencode([{
    name      = "batch"
    image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential = true
    # drop --with-sample once vendor/FRED extractors populate the landing zone
    command   = ["run", "--as-of", "today", "--with-sample"]
    environment = [
      { name = "QR_ENV", value = var.environment },
      { name = "QR_RAW_PATH", value = "/app/data/raw" },
      { name = "QR_S3_BUCKET", value = aws_s3_bucket.data.bucket }
    ]
    secrets = [{ name = "QR_DATABASE_URL", valueFrom = aws_secretsmanager_secret.db_url.arn }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.batch.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "batch"
      }
    }
  }])
}

# ------------------------------------------------------------------ schedule
data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecs:RunTask"], Resource = [aws_ecs_task_definition.batch.arn_without_revision, "${aws_ecs_task_definition.batch.arn_without_revision}:*"] },
      { Effect = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.execution.arn, aws_iam_role.task.arn] }
    ]
  })
}

resource "aws_scheduler_schedule" "daily" {
  name                         = "${local.name}-daily-risk-run"
  schedule_expression          = "cron(30 17 ? * MON-FRI *)"
  schedule_expression_timezone = "America/Chicago"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_ecs_cluster.main.arn
    role_arn = aws_iam_role.scheduler.arn
    retry_policy { maximum_retry_attempts = 2 }
    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.batch.arn_without_revision
      launch_type         = "FARGATE"
      network_configuration {
        subnets          = var.private_subnet_ids
        security_groups  = [aws_security_group.task.id]
        assign_public_ip = false
      }
    }
  }
}

# ------------------------------------------------------------------ monitoring
resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_log_metric_filter" "succeeded" {
  name           = "${local.name}-run-succeeded"
  log_group_name = aws_cloudwatch_log_group.batch.name
  pattern        = "{ $.msg = \"run succeeded\" }"
  metric_transformation {
    name      = "RunSucceeded"
    namespace = "QuantRisk"
    value     = "1"
  }
}

resource "aws_cloudwatch_log_metric_filter" "failed" {
  name           = "${local.name}-run-failed"
  log_group_name = aws_cloudwatch_log_group.batch.name
  pattern        = "{ ($.msg = \"run failed\") || ($.msg = \"run held on data quality\") }"
  metric_transformation {
    name      = "RunFailed"
    namespace = "QuantRisk"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "failed" {
  alarm_name          = "${local.name}-run-failed"
  namespace           = "QuantRisk"
  metric_name         = "RunFailed"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  alarm_description   = "Daily risk run failed or was held on a critical data-quality check."
}

resource "aws_cloudwatch_metric_alarm" "missing" {
  alarm_name          = "${local.name}-run-missing"
  namespace           = "QuantRisk"
  metric_name         = "RunSucceeded"
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  alarm_description   = "No successful risk run in the last 24 hours (weekdays)."
}
