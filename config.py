"""
TMOM Deviation Engine — Configuration

Environment-driven configuration for the standalone deviation engine service.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Backend connection (Vallab's tmom-app-backend)
    BACKEND_BASE_URL: str = os.getenv("TMOM_BACKEND_BASE_URL", "https://tmom-app-backend.onrender.com")
    BACKEND_WS_BASE_URL: str = os.getenv("TMOM_BACKEND_WS_BASE_URL", "")

    # Rule Engine connection (Abhinav's Rule-Engine)
    RULE_ENGINE_BASE_URL: str = os.getenv("TMOM_RULE_ENGINE_BASE_URL", "https://rule-engine-rcg9.onrender.com")

    # This service
    HOST: str = os.getenv("DEVIATION_ENGINE_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("DEVIATION_ENGINE_PORT", "8100"))
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

    # Timing defaults (seconds)
    DEFAULT_EXPIRY_WINDOW: float = float(os.getenv("DEFAULT_EXPIRY_WINDOW", "60.0"))
    LATE_ENTRY_GRACE_MS: float = float(os.getenv("LATE_ENTRY_GRACE_MS", "30000"))

    @property
    def backend_ws_url(self) -> str:
        if self.BACKEND_WS_BASE_URL:
            return self.BACKEND_WS_BASE_URL
        # Derive WS URL from HTTP URL
        url = self.BACKEND_BASE_URL
        if url.startswith("https://"):
            return f"wss://{url[len('https://'):]}"
        if url.startswith("http://"):
            return f"ws://{url[len('http://'):]}"
        return url


settings = Settings()
