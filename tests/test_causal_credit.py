import sys
import importlib
from unittest.mock import AsyncMock, MagicMock, patch

# Clear any previous import of routes.feedback to force a reload with our stubbed fastapi
if "routes.feedback" in sys.modules:
    del sys.modules["routes.feedback"]

# Mock fastapi before importing routes.feedback
_mock_fastapi = MagicMock()
_mock_router = MagicMock()
_mock_router.post.return_value = lambda fn: fn
_mock_router.get.return_value = lambda fn: fn
_mock_fastapi.APIRouter.return_value = _mock_router
sys.modules["fastapi"] = _mock_fastapi

import pytest
import json

import routes.feedback
importlib.reload(routes.feedback)
from routes.feedback import submit_feedback, FeedbackRequest
import state

@pytest.mark.asyncio
async def test_causal_credit_assignment_positive_diff():
    # Setup mocks for Redis and DB pool
    mock_redis = AsyncMock()
    # Mock return value of hgetall for moe:response:{response_id}
    # It must contain the causal_intervention metadata
    intervention_data = {
        "is_intervention": True,
        "intervened": "intervened_model@node",
        "default": "default_model@node",
        "expert": "math"
    }
    
    mock_redis.hgetall.side_effect = lambda key: {
        b"expert_models_used": b'["intervened_model@node::math"]',
        b"causal_intervention": json.dumps(intervention_data).encode("utf-8")
    } if "moe:response:" in key else {
        b"total": b"10",
        b"positive": b"8",  # 80% positive rate (estimated average 4.2)
        b"negative": b"1"
    }

    mock_db = MagicMock()
    mock_conn = MagicMock()
    mock_cur = AsyncMock()
    
    # Mock SELECT AVG(user_rating) from routing_telemetry
    # Let's say historical average rating is 3.5
    mock_cur.fetchone.return_value = (3.5,)
    mock_conn.cursor.return_value.__aenter__.return_value = mock_cur
    mock_db.connection.return_value.__aenter__.return_value = mock_conn

    with patch("state.redis_client", mock_redis), \
         patch("state._userdb_pool", mock_db), \
         patch("routes.feedback._record_expert_outcome", AsyncMock()) as mock_normal_outcome, \
         patch("services.inference._record_expert_outcome", AsyncMock()) as mock_causal_outcome:
         
        # Invoke feedback handler directly
        # Actual rating = 5, historical avg = 3.5 -> diff = +1.5 -> positive reward
        req = FeedbackRequest(response_id="chatcmpl-test", rating=5)
        response = await submit_feedback(req)
        assert response["status"] == "ok"
        
        # Verify that normal outcome recorded it
        mock_normal_outcome.assert_any_call("intervened_model@node", "math", True)
        # Verify that causal positive reward was recorded for intervened model
        mock_causal_outcome.assert_any_call("intervened_model@node", "math", True)


@pytest.mark.asyncio
async def test_causal_credit_assignment_negative_diff():
    # Setup mocks
    mock_redis = AsyncMock()
    intervention_data = {
        "is_intervention": True,
        "intervened": "intervened_model@node",
        "default": "default_model@node",
        "expert": "math"
    }
    
    mock_redis.hgetall.side_effect = lambda key: {
        b"expert_models_used": b'["intervened_model@node::math"]',
        b"causal_intervention": json.dumps(intervention_data).encode("utf-8")
    } if "moe:response:" in key else {
        b"total": b"10",
        b"positive": b"8",
        b"negative": b"1"
    }

    mock_db = MagicMock()
    mock_conn = MagicMock()
    mock_cur = AsyncMock()
    
    # Historical average rating = 4.5
    mock_cur.fetchone.return_value = (4.5,)
    mock_conn.cursor.return_value.__aenter__.return_value = mock_cur
    mock_db.connection.return_value.__aenter__.return_value = mock_conn

    with patch("state.redis_client", mock_redis), \
         patch("state._userdb_pool", mock_db), \
         patch("routes.feedback._record_expert_outcome", AsyncMock()) as mock_normal_outcome, \
         patch("services.inference._record_expert_outcome", AsyncMock()) as mock_causal_outcome:
         
        # Invoke feedback handler directly
        # rating = 3, historical avg = 4.5 -> diff = -1.5 -> penalty (False)
        req = FeedbackRequest(response_id="chatcmpl-test", rating=3)
        response = await submit_feedback(req)
        assert response["status"] == "ok"
        
        # Verify that normal outcome recorded it (positive is False since rating is 3)
        mock_normal_outcome.assert_any_call("intervened_model@node", "math", False)
        # Verify that causal penalty was recorded for intervened model
        mock_causal_outcome.assert_any_call("intervened_model@node", "math", False)
