# Azure deployment design

The same container image runs unchanged on Azure. Only the storage upload (S3 to Blob) and the secret source differ.

| Job | AWS (Terraform in `../terraform/aws`) | Azure equivalent |
|---|---|---|
| Schedule the daily batch | EventBridge Scheduler, `cron(30 17 ? * MON-FRI *)` America/Chicago | **Container Apps Job** with a cron trigger `30 22 * * 1-5` (UTC; adjust for DST), or a Data Factory schedule trigger |
| Run the container | ECS Fargate task | Container Apps Job replica (2 vCPU / 4 GiB) |
| Image registry | ECR (immutable tags) | Azure Container Registry |
| Database | RDS for PostgreSQL 16 | Azure Database for PostgreSQL Flexible Server 16 |
| Files and reports | S3 (versioned, KMS) | ADLS Gen2 / Blob Storage (versioning, CMK) |
| Secrets | Secrets Manager | Key Vault, read through a managed identity |
| Logs | CloudWatch Logs | Log Analytics workspace (Container Apps sends stdout JSON automatically) |
| Alerts | Metric filters, alarms, SNS | Log Analytics scheduled query rules, Action Group (email or Teams) |
| Dashboard | Streamlit on ECS or App Runner | **Power BI** on the `v_*` reporting views (DirectQuery or scheduled refresh through an on-premises data gateway or VNet gateway), or Streamlit on App Service |
| IaC | Terraform | Terraform `azurerm` or Bicep |

## Alert query (Kusto)

```kusto
ContainerAppConsoleLogs_CL
| where ContainerJobName_s == "quantrisk-batch"
| extend j = parse_json(Log_s)
| where j.msg in ("run failed", "run held on data quality")
| project TimeGenerated, run_id = tostring(j.run_id), msg = tostring(j.msg)
```

## Power BI

Connect to the PostgreSQL server and import these views: `v_daily_risk_summary`, `v_limit_status`, `v_krd_by_tenor` and `v_stress_by_scenario`. Because the views read only persisted results, Power BI always matches the HTML report for the same `run_id`.
