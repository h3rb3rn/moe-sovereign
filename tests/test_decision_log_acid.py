"""Tests for ACID decision logging."""

import os
import sqlite3
import uuid
import pytest
from services.decision_log import initialize_wal_db, log_decision_acid

def test_initialize_wal_db(tmp_path):
    """Test that initialize_wal_db creates DB with WAL mode."""
    db_path = str(tmp_path / "decisions.db")
    initialize_wal_db(db_path)
    
    assert os.path.exists(db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"

def test_log_decision_acid_writes_and_reads(tmp_path):
    """Test that log_decision_acid writes and can be read correctly."""
    db_path = str(tmp_path / "decisions.db")
    initialize_wal_db(db_path)
    
    event = {
        "task_id": "task-123",
        "decision": "approve",
        "metadata": {"reason": "ok"}
    }
    
    record_id = log_decision_acid(db_path, event)
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM decisions WHERE id = ?", (record_id,))
        row = cursor.fetchone()
        
    assert row is not None
    assert row["id"] == record_id
    assert row["task_id"] == "task-123"
    assert row["decision"] == "approve"
    assert row["metadata"] == '{"reason": "ok"}'
    assert row["created_at"] > 0

def test_log_decision_acid_returns_valid_uuid(tmp_path):
    """Test that log_decision_acid returns a valid UUID string."""
    db_path = str(tmp_path / "decisions.db")
    initialize_wal_db(db_path)
    
    event = {"task_id": "t1", "decision": "d1"}
    record_id = log_decision_acid(db_path, event)
    
    parsed = uuid.UUID(record_id)
    assert str(parsed) == record_id

def test_log_decision_acid_missing_task_id(tmp_path):
    """Test that missing task_id raises ValueError."""
    db_path = str(tmp_path / "decisions.db")
    initialize_wal_db(db_path)
    
    with pytest.raises(ValueError, match="task_id and decision must be provided"):
        log_decision_acid(db_path, {"decision": "d1"})

def test_log_decision_acid_missing_decision(tmp_path):
    """Test that missing decision raises ValueError."""
    db_path = str(tmp_path / "decisions.db")
    initialize_wal_db(db_path)
    
    with pytest.raises(ValueError, match="task_id and decision must be provided"):
        log_decision_acid(db_path, {"task_id": "t1"})

def test_log_decision_acid_multiple_events_unique_uuids(tmp_path):
    """Test that multiple events get unique UUIDs."""
    db_path = str(tmp_path / "decisions.db")
    initialize_wal_db(db_path)
    
    ids = set()
    for i in range(5):
        record_id = log_decision_acid(db_path, {"task_id": f"t{i}", "decision": "d"})
        ids.add(record_id)
        
    assert len(ids) == 5
