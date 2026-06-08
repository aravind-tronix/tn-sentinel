# ── Incidents table ────────────────────────────────────────────────────────
resource "aws_dynamodb_table" "incidents" {
  name         = "${var.project}-incidents-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "district"
  range_key    = "published_at_id" # e.g. "2025-06-08T10:00:00#421"

  attribute {
    name = "district"
    type = "S"
  }

  attribute {
    name = "published_at_id"
    type = "S"
  }

  # Used by category-time-index
  attribute {
    name = "category"
    type = "S"
  }

  # Composite: "{category}#{published_at}" — used for district+category filter
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

  # Filter by category + time range across all districts
  global_secondary_index {
    name            = "category-time-index"
    hash_key        = "category"
    range_key       = "published_at"
    projection_type = "ALL"
  }

  # Filter by district + category using begins_with(cat_time, "Assault#")
  global_secondary_index {
    name            = "district-category-time-index"
    hash_key        = "district"
    range_key       = "cat_time"
    projection_type = "ALL"
  }

  # Filter by source
  global_secondary_index {
    name            = "source-time-index"
    hash_key        = "source_id"
    range_key       = "published_at"
    projection_type = "ALL"
  }

  # Exact ID lookup for GET /incidents/{id}
  global_secondary_index {
    name            = "id-index"
    hash_key        = "id"
    projection_type = "ALL"
  }

  tags = {
    Environment = var.environment
  }
}

# ── WebSocket connections table ────────────────────────────────────────────
resource "aws_dynamodb_table" "ws_connections" {
  name         = "${var.project}-ws-connections-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "connectionId"

  attribute {
    name = "connectionId"
    type = "S"
  }

  # Auto-expire stale connections (set ttl = epoch + 24h when writing)
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = {
    Environment = var.environment
  }
}
