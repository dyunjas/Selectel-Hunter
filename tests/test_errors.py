from app.selectel.errors import ErrorClassifier, ErrorType


def test_no_free_ip_by_neutron_payload():
    assert ErrorClassifier.classify(400, {"NeutronError": {"type": "IpAddressGenerationFailure", "message": "No more IP addresses"}}) == ErrorType.NO_FREE_IP


def test_http_classes():
    assert ErrorClassifier.classify(401, {}) == ErrorType.AUTH_ERROR
    assert ErrorClassifier.classify(403, {}) == ErrorType.PERMISSION_ERROR
    assert ErrorClassifier.classify(429, {}) == ErrorType.RATE_LIMIT
