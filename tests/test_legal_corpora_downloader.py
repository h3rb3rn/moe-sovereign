"""Unit tests for the Legal Open-Access Corpus Downloader & Formatter."""

import json
import os
import tempfile
import pytest

from scripts.download_legal_corpora import (
    CORPUS_REGISTRY,
    TARGET_SCHEMA_KEYS,
    compile_corpus_to_jsonl,
    create_record,
    download_file_with_resume,
)


def test_corpus_registry_entries():
    """Ensure all registry entries have valid keys, URLs, and licenses."""
    assert len(CORPUS_REGISTRY) >= 5
    for entry in CORPUS_REGISTRY:
        assert "name" in entry
        assert "category" in entry
        assert "license" in entry
        assert "url" in entry
        assert entry["url"].startswith("http")


def test_create_record_schema():
    """Verify create_record returns a schema-compliant dictionary."""
    rec = create_record(
        doc_id="test_001",
        source="unit_test",
        license_type="CC0",
        category="testing",
        title="Test Title",
        text="Test text content",
        metadata={"key": "val"},
    )
    assert set(rec.keys()).issuperset(TARGET_SCHEMA_KEYS)
    assert rec["id"] == "test_001"
    assert rec["title"] == "Test Title"
    assert rec["text"] == "Test text content"


def test_compile_corpus_to_jsonl():
    """Test compiling records to a temporary JSONL file."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_file = os.path.join(tmp_dir, "test_corpus.jsonl")
        count = compile_corpus_to_jsonl(out_file, records_per_source=10)
        assert count == len(CORPUS_REGISTRY) * 10
        assert os.path.exists(out_file)

        # Validate line formats
        with open(out_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            assert len(lines) == count
            for line in lines:
                data = json.loads(line)
                assert "id" in data
                assert "text" in data
                assert "license" in data
