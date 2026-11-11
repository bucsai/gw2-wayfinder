import json

import pytest
import respx
from fastapi.testclient import TestClient
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app import agent as agent_module
from app import config
from app.gw2 import BASE_URL
from tests.test_agent import KEY, gw2_api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with respx.mock:
        respx.route(host="testserver").pass_through()
        respx.route(url__startswith=BASE_URL).mock(side_effect=gw2_api)
        from app.main import app

        with TestClient(app) as client:
            yield client


async def reply(messages, info: AgentInfo):
    yield "Hello Character_A\nof Guild_A"


def test_session_and_chat_restore_real_names(client):
    response = client.post("/api/session", headers={"X-GW2-Key": KEY})
    assert response.status_code == 200
    session_id = response.json()["session_id"]

    with agent_module.agent.override(model=FunctionModel(stream_function=reply)):
        response = client.post(
            "/api/chat", headers={"X-GW2-Key": KEY}, json={"session_id": session_id, "message": "hi"}
        )
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events == [{"text": "Hello Zoe Lightfoot\n"}, {"text": "of Knights of Ascalon"}]
    assert response.headers["Content-Security-Policy"].startswith("default-src 'self'")


def test_chat_requires_matching_key(client):
    session_id = client.post("/api/session", headers={"X-GW2-Key": KEY}).json()["session_id"]
    response = client.post(
        "/api/chat", headers={"X-GW2-Key": "other"}, json={"session_id": session_id, "message": "hi"}
    )
    assert response.status_code == 401
    assert client.post("/api/chat", json={"session_id": session_id, "message": "hi"}).status_code == 422


def test_invalid_key_is_rejected(client):
    response = client.post("/api/session", headers={"X-GW2-Key": "bad"})
    assert response.status_code == 401


def test_static_page(client):
    assert "GW2 Wayfinder" in client.get("/").text
