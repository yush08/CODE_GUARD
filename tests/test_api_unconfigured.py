"""API behaviour that must hold WITHOUT a database (unconfigured mode).

These tests assert the honest failure contract: health/system endpoints work
and report the database state, while every DB-dependent endpoint answers with
the standard error envelope (503) instead of fabricating data.
"""

from __future__ import annotations


def test_health_reports_application_alive_and_database_state(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"]["config_state"] in {
        "UNCONFIGURED_MISSING_URL",
        "UNCONFIGURED_PLACEHOLDER",
        "INVALID_URL",
        "CONFIGURED",
    }
    assert body["database"]["connectivity"] in {"NOT_ATTEMPTED", "REACHABLE", "UNREACHABLE"}
    assert body["database_required_for_full_function"] is True
    # credentials are never echoed back
    assert "password" not in str(body).lower() or "****" in str(body)


def test_system_status_is_truthful(client):
    r = client.get("/api/v1/system/status")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {
        "app", "version", "environment", "os", "python", "auth_mode",
        "running_as_admin", "database", "compilers", "supported_languages",
        "job_object", "execution_worker", "capabilities", "warnings",
    }
    assert body["auth_mode"] == "local_dev"
    assert body["supported_languages"] == ["c", "cpp"]
    assert len(body["compilers"]) == 2
    # capability statuses must come from the honest vocabulary
    allowed = {"ACTIVE", "MEASURED_ONLY", "PARTIAL", "NOT_AVAILABLE", "RESERVED"}
    for cap in body["capabilities"]:
        assert cap["status"] in allowed, cap
        assert cap["detail"]
    ids = {c["id"] for c in body["capabilities"]}
    assert "network_isolation" in ids  # documented as NOT_AVAILABLE
    assert "auth_rbac" in ids  # documented as PARTIAL
    # Job Objects are never described as a full sandbox
    joined = " ".join(w.lower() for w in body["warnings"])
    assert "not a full sandbox" in joined


def test_database_dependent_endpoints_return_503_envelope(unconfigured_client):
    cases = [
        ("GET", "/api/v1/problems", None),
        ("GET", "/api/v1/problems/1", None),
        ("POST", "/api/v1/problems", {"title": "x"}),
        ("GET", "/api/v1/submissions", None),
        ("POST", "/api/v1/submissions", {"problem_id": 1, "language": "c", "source_code": "int main(){return 0;}"}),
        ("POST", "/api/v1/submissions/run", {"language": "c", "source_code": "int main(){return 0;}"}),
        ("GET", "/api/v1/faculty/dashboard", None),
        ("GET", "/api/v1/faculty/alerts", None),
        ("GET", "/api/v1/faculty/trust-history?workspace_label=demo", None),
        ("GET", "/api/v1/analysis/submissions/1/report", None),
        ("GET", "/api/v1/analysis/submissions/1/fingerprint", None),
        ("GET", "/api/v1/analysis/submissions/1/events", None),
        ("GET", "/api/v1/analysis/submissions/1/sequence", None),
        ("GET", "/api/v1/analysis/submissions/1/hardcoding", None),
    ]
    for method, url, payload in cases:
        r = unconfigured_client.request(method, url, json=payload)
        assert r.status_code == 503, f"{method} {url} -> {r.status_code}"
        body = r.json()
        assert body["error"]["code"] in {
            "DATABASE_NOT_CONFIGURED",
            "DATABASE_UNAVAILABLE",
        }, body
        assert body["error"]["message"]
        assert body["request_id"]
        assert r.headers.get("X-Request-ID") == body["request_id"]


def test_validation_errors_use_the_same_envelope(client):
    # System endpoints have no database dependency, so request validation is
    # observable even in database-unconfigured mode. (DB endpoints answer 503
    # first — the service is unavailable, so the envelope reports that.)
    r = client.get("/api/v1/system/status?refresh=not-a-bool")
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"]["issues"]
    assert body["request_id"]


def test_unknown_route_uses_the_error_envelope(client):
    r = client.get("/api/v1/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["request_id"]


def test_cors_origins_are_environment_driven(client):
    r = client.get(
        "/api/v1/health", headers={"Origin": "http://localhost:5173"}
    )
    assert r.status_code == 200
    # default dev origins come from settings, not hardcoded routes
    assert "access-control-allow-origin" in r.headers
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"

    r2 = client.get("/api/v1/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r2.headers


def test_openapi_documents_every_route_group(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert "/api/v1/health" in paths
    assert "/api/v1/system/status" in paths
    assert "/api/v1/problems" in paths
    assert "/api/v1/submissions" in paths
    assert "/api/v1/submissions/run" in paths
    assert "/api/v1/faculty/dashboard" in paths
    assert "/api/v1/analysis/submissions/{submission_id}/report" in paths
    # statuses are documented on the submission schema
    statuses = spec["components"]["schemas"]["SubmissionStatus"]["enum"]
    for expected in (
        "QUEUED", "COMPILING", "RUNNING", "ANALYZING", "ACCEPTED",
        "WRONG_ANSWER", "COMPILATION_ERROR", "RUNTIME_ERROR",
        "TIME_LIMIT_EXCEEDED", "MEMORY_LIMIT_EXCEEDED",
        "PROCESS_LIMIT_EXCEEDED", "SECURITY_VIOLATION", "CANCELLED",
        "INTERNAL_ERROR",
    ):
        assert expected in statuses


def test_queue_snapshot_exposed_on_system_status(client):
    body = client.get("/api/v1/system/status").json()
    worker = body["execution_worker"]
    assert set(worker) == {
        "running",
        "active_executions",
        "queued_jobs",
        "max_concurrent_executions",
        "max_queued_jobs",
    }
    assert worker["max_queued_jobs"] >= 1
