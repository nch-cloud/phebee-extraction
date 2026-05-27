#!/bin/bash
# Build all mock Docker images for testing

set -e  # Exit on error

echo "Building PheBee mock extraction tools..."
echo ""

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Mock extractor (keyword-based)
echo "1/2 Building mock extractor (phebee-mock-extractor:test)..."
docker build -f Dockerfile.mock -t phebee-mock-extractor:test . --quiet
echo "✓ Mock extractor built"
echo ""

# Context classifier
echo "2/2 Building mock context classifier (phebee-mock-context:test)..."
docker build -f Dockerfile.mock-context -t phebee-mock-context:test . --quiet
echo "✓ Mock context built"
echo ""

echo "================================================"
echo "All mock images built successfully!"
echo "================================================"
echo ""
docker images | grep -E "REPOSITORY|phebee-mock"
echo ""
echo "Run tests with: pytest tests/test_integration.py -v"
