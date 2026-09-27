from cryptography.fernet import Fernet


class SecretBox:
    def __init__(self, key: str):
        if not key:
            raise ValueError("ENCRYPTION_KEY is required")
        self._fernet = Fernet(key.encode())

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        return self._fernet.decrypt(value.encode()).decode()
