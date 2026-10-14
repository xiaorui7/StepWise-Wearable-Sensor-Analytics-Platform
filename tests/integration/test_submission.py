from pathlib import Path

import pytest
from test_api_validation import NoopQueue, build_client

FIXTURES = Path(__file__).parents[2] / "fixtures" / "synthetic"


def submit(client, key=None, text=None, config="{}", name="walk.txt"):
    return client.post(
        "/api/v1/analyses",
        headers={"Idempotency-Key": key} if key else {},
        files={
            "walking_file": (
                name,
                text or (FIXTURES / "normal_like.synthetic.txt").read_bytes(),
                "text/plain",
            )
        },
        data={"config_json": config},
    )


def test_api_idempotency_replay_and_conflict(tmp_path):
    client, _ = build_client(tmp_path, NoopQueue())
    with client:
        first = submit(client, "same-key")
        second = submit(client, "same-key")
        assert first.status_code == second.status_code == 202
        assert first.json()["analysis_id"] == second.json()["analysis_id"]
        conflict = submit(client, "same-key", config='{"smooth_window":5}')
        assert conflict.status_code == 409
        independent = submit(client)
        assert independent.json()["analysis_id"] != first.json()["analysis_id"]
        assert client.get("/api/v1/analyses").json()["total"] == 2


@pytest.mark.parametrize(
    "body", [b"1 12:00:00.000 1 2", b"malformed", b" ", b"1 12:00:00.000 inf 1 1 1 1 1 1 1 1 1 1 1 1"]
)
def test_malformed_data_rejected_before_persistence(tmp_path, body):
    client, _ = build_client(tmp_path, NoopQueue())
    with client:
        assert submit(client, text=body).status_code == 422
        assert client.get("/api/v1/analyses").json()["total"] == 0


def test_validation_hides_private_config_and_rejects_format(tmp_path):
    client, _ = build_client(tmp_path, NoopQueue())
    with client:
        response = submit(client, config='{"private-local-path":"secret"}')
        assert response.status_code == 422 and "secret" not in response.text
        assert submit(client, name="walk.exe").status_code == 415
        assert submit(client, key="x" * 129).status_code == 422


def test_oversized_chunked_request_is_bounded(tmp_path):
    client, _ = build_client(tmp_path, NoopQueue())
    with client:
        response = client.post(
            "/api/v1/analyses/text",
            content=(b"x" * 1024 * 1024 for _ in range(11)),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413


def test_request_id_is_returned_and_persisted(tmp_path):
    client, _ = build_client(tmp_path, NoopQueue())
    with client:
        response = client.post(
            "/api/v1/analyses",
            headers={"X-Request-ID": "test-correlation"},
            files={
                "walking_file": (
                    "walk.txt",
                    (FIXTURES / "normal_like.synthetic.txt").read_bytes(),
                    "text/plain",
                )
            },
        )
        assert response.headers["X-Request-ID"] == "test-correlation"
        status = client.get(f"/api/v1/analyses/{response.json()['analysis_id']}").json()
        assert status["input_metadata"]["request_id"] == "test-correlation"
        assert status["attempts"] == 0
