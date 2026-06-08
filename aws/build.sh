#!/usr/bin/env bash
# Bundle aws/ handlers + pip dependencies into terraform/.build/lambda.zip
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$ROOT/terraform/.build/lambda_pkg"
ZIP_OUT="$ROOT/terraform/.build/lambda.zip"

echo "[build] cleaning $BUILD_DIR"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

echo "[build] installing Lambda dependencies"
pip install fastapi mangum boto3 --target "$BUILD_DIR" -q

echo "[build] copying handler files"
cp "$ROOT/aws/"*.py "$BUILD_DIR/"

echo "[build] zipping"
cd "$BUILD_DIR"
zip -r "$ZIP_OUT" . -x "*.pyc" -x "*/__pycache__/*" > /dev/null

echo "[build] done → $ZIP_OUT"
