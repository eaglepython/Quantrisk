output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "data_bucket" {
  value = aws_s3_bucket.data.bucket
}

output "db_endpoint" {
  value = aws_db_instance.pg.address
}

output "log_group" {
  value = aws_cloudwatch_log_group.batch.name
}

output "schedule" {
  value = aws_scheduler_schedule.daily.name
}
