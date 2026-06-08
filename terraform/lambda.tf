data "archive_file" "placeholder" {
  type        = "zip"
  source_file = "${path.module}/lambda_src/placeholder.py"
  output_path = "${path.module}/.build/placeholder.zip"
}

# ── API Lambda (FastAPI + Mangum) ──────────────────────────────────────────
resource "aws_lambda_function" "api" {
  function_name    = "${var.project}-api-${var.environment}"
  role             = aws_iam_role.lambda_exec.arn
  runtime          = "python3.12"
  handler          = "placeholder.handler"
  filename         = data.archive_file.placeholder.output_path
  source_code_hash = data.archive_file.placeholder.output_base64sha256
  memory_size      = 256
  timeout          = 29 # API GW HTTP API hard limit

  environment {
    variables = {
      INCIDENTS_TABLE      = aws_dynamodb_table.incidents.name
      WS_CONNECTIONS_TABLE = aws_dynamodb_table.ws_connections.name
      WS_ENDPOINT          = "${aws_apigatewayv2_api.websocket.api_endpoint}/${var.environment}"
      AWS_REGION_NAME      = var.aws_region
      ENVIRONMENT          = var.environment
    }
  }

  tags = {
    Environment = var.environment
  }
}

# ── WebSocket Lambda ($connect / $disconnect / $default) ───────────────────
resource "aws_lambda_function" "websocket" {
  function_name    = "${var.project}-websocket-${var.environment}"
  role             = aws_iam_role.lambda_exec.arn
  runtime          = "python3.12"
  handler          = "placeholder.handler"
  filename         = data.archive_file.placeholder.output_path
  source_code_hash = data.archive_file.placeholder.output_base64sha256
  memory_size      = 128
  timeout          = 10

  environment {
    variables = {
      WS_CONNECTIONS_TABLE = aws_dynamodb_table.ws_connections.name
      WS_ENDPOINT          = "${aws_apigatewayv2_api.websocket.api_endpoint}/${var.environment}"
      AWS_REGION_NAME      = var.aws_region
      ENVIRONMENT          = var.environment
    }
  }

  tags = {
    Environment = var.environment
  }
}

# ── Permissions for API Gateway to invoke Lambdas ─────────────────────────
resource "aws_lambda_permission" "api_gw_invoke" {
  statement_id  = "AllowHTTPAPIInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*"
}

resource "aws_lambda_permission" "websocket_gw_invoke" {
  statement_id  = "AllowWebSocketAPIInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.websocket.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.websocket.execution_arn}/*"
}
