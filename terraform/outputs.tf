output "http_api_url" {
  description = "REST API base URL — $default stage, no /prod prefix"
  value       = aws_apigatewayv2_api.http.api_endpoint
}

output "websocket_url" {
  description = "WebSocket URL (use in React UI)"
  value       = aws_apigatewayv2_stage.websocket.invoke_url
}

output "websocket_endpoint" {
  description = "WebSocket Management API endpoint (use in local worker to push events)"
  value       = "${aws_apigatewayv2_api.websocket.api_endpoint}/${var.environment}"
}

output "incidents_table_name" {
  description = "DynamoDB incidents table name"
  value       = aws_dynamodb_table.incidents.name
}

output "ws_connections_table_name" {
  description = "DynamoDB WebSocket connections table name"
  value       = aws_dynamodb_table.ws_connections.name
}

output "api_lambda_name" {
  description = "API Lambda function name (for code deployments)"
  value       = aws_lambda_function.api.function_name
}

output "websocket_lambda_name" {
  description = "WebSocket Lambda function name (for code deployments)"
  value       = aws_lambda_function.websocket.function_name
}
