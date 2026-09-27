from enum import StrEnum


class ErrorType(StrEnum):
    NO_FREE_IP = "NO_FREE_IP"; AUTH_ERROR = "AUTH_ERROR"; PERMISSION_ERROR = "PERMISSION_ERROR"; RATE_LIMIT = "RATE_LIMIT"; NETWORK_ERROR = "NETWORK_ERROR"; SERVER_ERROR = "SERVER_ERROR"; UNKNOWN = "UNKNOWN"


class SelectelError(Exception):
    def __init__(self, kind: ErrorType, message: str, retry_after: float | None = None, status: int | None = None):
        super().__init__(message); self.kind = kind; self.retry_after = retry_after; self.status = status


class ErrorClassifier:
    @staticmethod
    def classify(status: int, payload: dict) -> ErrorType:
        error = payload.get("NeutronError", {}) if isinstance(payload, dict) else {}
        text = f"{error.get('type', '')} {error.get('message', '')}".lower()
        if status == 409 or any(x in text for x in ("ipaddressgenerationfailure", "no more ip addresses", "no ip addresses available", "address generation failure")): return ErrorType.NO_FREE_IP
        if status == 401: return ErrorType.AUTH_ERROR
        if status == 403: return ErrorType.PERMISSION_ERROR
        if status == 429: return ErrorType.RATE_LIMIT
        if status >= 500: return ErrorType.SERVER_ERROR
        return ErrorType.UNKNOWN
