# Changelog

All notable changes to this project will be documented in this file.

## [1.0.0] - 2026-05-27

### Initial Release

- Local extraction orchestrator for clinical notes
- Support for JSON, JSONL, Parquet, CSV input formats
- Tool chaining support (e.g., extraction → context classification)
- Batch processing with configurable batch sizes
- GPU support via Docker
- **PheBee format output by default** - ready for bulk import (streaming transformation)
- **Flat format option** - note-centric output with --flat-format flag
- Comprehensive test suite (48 tests: 37 extract unit tests, 11 integration tests)
- Mock extraction tools for testing
- INTERFACE.md specification for Docker containers
- Modern Python tooling: uv package manager, ruff linter/formatter
- pyproject.toml for standardized project configuration
