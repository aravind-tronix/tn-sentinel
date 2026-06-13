# ── Dev environment — mirrors prod but isolated ────────────────────────────
# All resources suffixed -dev. DynamoDB tables are separate so dev data
# never mixes with production.

locals {
  dev = "dev"
}

# ── DynamoDB — dev ─────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "incidents_dev" {
  name         = "${var.project}-incidents-${local.dev}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "district"
  range_key    = "published_at_id"

  attribute {
    name = "district"
    type = "S"
  }
  attribute {
    name = "published_at_id"
    type = "S"
  }
  attribute {
    name = "category"
    type = "S"
  }
  attribute {
    name = "cat_time"
    type = "S"
  }
  attribute {
    name = "source_id"
    type = "S"
  }
  attribute {
    name = "published_at"
    type = "S"
  }
  attribute {
    name = "id"
    type = "S"
  }

  global_secondary_index {
    name            = "category-time-index"
    hash_key        = "category"
    range_key       = "published_at"
    projection_type = "ALL"
  }
  global_secondary_index {
    name            = "district-category-time-index"
    hash_key        = "district"
    range_key       = "cat_time"
    projection_type = "ALL"
  }
  global_secondary_index {
    name            = "source-time-index"
    hash_key        = "source_id"
    range_key       = "published_at"
    projection_type = "ALL"
  }
  global_secondary_index {
    name            = "id-index"
    hash_key        = "id"
    projection_type = "ALL"
  }

  tags = { Environment = local.dev }
}

resource "aws_dynamodb_table" "ws_connections_dev" {
  name         = "${var.project}-ws-connections-${local.dev}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "connectionId"

  attribute {
    name = "connectionId"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = { Environment = local.dev }
}

# ── Lambda — dev ───────────────────────────────────────────────────────────

resource "aws_lambda_function" "api_dev" {
  function_name    = "${var.project}-api-${local.dev}"
  role             = aws_iam_role.lambda_exec.arn
  runtime          = "python3.12"
  handler          = "api_handler.lambda_handler"
  filename         = local.lambda_zip
  source_code_hash = local.lambda_hash
  memory_size      = 256
  timeout          = 29

  environment {
    variables = {
      INCIDENTS_TABLE      = aws_dynamodb_table.incidents_dev.name
      WS_CONNECTIONS_TABLE = aws_dynamodb_table.ws_connections_dev.name
      WS_ENDPOINT          = "${aws_apigatewayv2_api.websocket_dev.api_endpoint}/${local.dev}"
      AWS_REGION_NAME      = var.aws_region
      ENVIRONMENT          = local.dev
      ALLOWED_ORIGINS      = "https://develop.${var.ui_domain},http://localhost:5173,http://localhost:8000"
    }
  }

  tags = { Environment = local.dev }
}

resource "aws_lambda_function" "websocket_dev" {
  function_name    = "${var.project}-websocket-${local.dev}"
  role             = aws_iam_role.lambda_exec.arn
  runtime          = "python3.12"
  handler          = "ws_handler.handler"
  filename         = local.lambda_zip
  source_code_hash = local.lambda_hash
  memory_size      = 128
  timeout          = 10

  environment {
    variables = {
      WS_CONNECTIONS_TABLE = aws_dynamodb_table.ws_connections_dev.name
      WS_ENDPOINT          = "${aws_apigatewayv2_api.websocket_dev.api_endpoint}/${local.dev}"
      AWS_REGION_NAME      = var.aws_region
      ENVIRONMENT          = local.dev
    }
  }

  tags = { Environment = local.dev }
}

# ── API Gateway — dev ──────────────────────────────────────────────────────

resource "aws_apigatewayv2_api" "http_dev" {
  name          = "${var.project}-http-${local.dev}"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = [
      "https://develop.${var.ui_domain}",
      "http://localhost:5173",
      "http://localhost:8000",
    ]
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["content-type", "x-api-key"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_integration" "http_lambda_dev" {
  api_id                 = aws_apigatewayv2_api.http_dev.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api_dev.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "proxy_dev" {
  api_id    = aws_apigatewayv2_api.http_dev.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.http_lambda_dev.id}"
}

resource "aws_apigatewayv2_stage" "http_dev" {
  api_id      = aws_apigatewayv2_api.http_dev.id
  name        = "$default"
  auto_deploy = true
}

# ── WebSocket API — dev ────────────────────────────────────────────────────

resource "aws_apigatewayv2_api" "websocket_dev" {
  name                       = "${var.project}-ws-${local.dev}"
  protocol_type              = "WEBSOCKET"
  route_selection_expression = "$request.body.action"
}

resource "aws_apigatewayv2_integration" "websocket_lambda_dev" {
  api_id           = aws_apigatewayv2_api.websocket_dev.id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.websocket_dev.invoke_arn
}

resource "aws_apigatewayv2_route" "ws_connect_dev" {
  api_id    = aws_apigatewayv2_api.websocket_dev.id
  route_key = "$connect"
  target    = "integrations/${aws_apigatewayv2_integration.websocket_lambda_dev.id}"
}

resource "aws_apigatewayv2_route" "ws_disconnect_dev" {
  api_id    = aws_apigatewayv2_api.websocket_dev.id
  route_key = "$disconnect"
  target    = "integrations/${aws_apigatewayv2_integration.websocket_lambda_dev.id}"
}

resource "aws_apigatewayv2_route" "ws_default_dev" {
  api_id    = aws_apigatewayv2_api.websocket_dev.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.websocket_lambda_dev.id}"
}

resource "aws_apigatewayv2_stage" "websocket_dev" {
  api_id      = aws_apigatewayv2_api.websocket_dev.id
  name        = local.dev
  auto_deploy = true
}

# ── Lambda permissions — dev ───────────────────────────────────────────────

resource "aws_lambda_permission" "api_gw_invoke_dev" {
  statement_id  = "AllowHTTPAPIInvokeDev"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api_dev.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http_dev.execution_arn}/*"
}

resource "aws_lambda_permission" "websocket_gw_invoke_dev" {
  statement_id  = "AllowWebSocketAPIInvokeDev"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.websocket_dev.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.websocket_dev.execution_arn}/*"
}

# ── Outputs — dev ──────────────────────────────────────────────────────────

output "http_api_url_dev" {
  value = aws_apigatewayv2_api.http_dev.api_endpoint
}

output "websocket_url_dev" {
  value = "${aws_apigatewayv2_api.websocket_dev.api_endpoint}/${local.dev}"
}
