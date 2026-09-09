import uvicorn

from espressolab.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "espressolab.portal.app:app",
        host=settings.portal_host,
        port=settings.portal_port,
        log_level="info",
    )
