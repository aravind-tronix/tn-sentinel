resource "aws_amplify_app" "ui" {
  name         = "${var.project}-ui"
  repository   = "https://github.com/aravind-tronix/tn-sentinel-ui"
  access_token = var.github_token

  build_spec = file("${path.module}/../amplify.yml.tpl")

  # Env vars injected at build time — Vite bakes them into the bundle
  environment_variables = {
    VITE_API_BASE_URL = aws_apigatewayv2_api.http.api_endpoint
    VITE_WS_URL       = "${aws_apigatewayv2_api.websocket.api_endpoint}/${var.environment}"
  }

  # SPA — redirect all paths to index.html
  custom_rule {
    source = "</^[^.]+$|\\.(?!(css|gif|ico|jpg|js|png|txt|svg|woff|woff2|ttf|map|json)$)([^.]+$)/>"
    status = "200"
    target = "/index.html"
  }

  tags = {
    Environment = var.environment
  }
}

resource "aws_amplify_branch" "main" {
  app_id      = aws_amplify_app.ui.id
  branch_name = "main"
  framework   = "React"
  stage       = "PRODUCTION"

  enable_auto_build = true
}

output "amplify_url" {
  description = "Amplify hosted UI URL"
  value       = "https://${aws_amplify_branch.main.branch_name}.${aws_amplify_app.ui.default_domain}"
}
