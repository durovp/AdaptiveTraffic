"""Single-process Uvicorn entry point for Docker/hosting; no auto-open browser."""
import os


def app_factory():
    from .web import create_app
    return create_app()


def main():
    import uvicorn
    from .server_config import ServerConfig
    policy=ServerConfig.from_env()
    host=os.getenv('HOST','127.0.0.1')
    if host not in ('127.0.0.1','localhost','::1') and not policy.production:
        raise ValueError('Для внешнего HOST включите TRAFFIC_ENV=production')
    uvicorn.run('adaptive_traffic.serve:app_factory', factory=True,
                host=host, port=int(os.getenv('PORT','8000')),
                workers=1, proxy_headers=True,
                forwarded_allow_ips=os.getenv('FORWARDED_ALLOW_IPS','127.0.0.1'),
                limit_concurrency=24, backlog=32, timeout_keep_alive=5,
                timeout_graceful_shutdown=15)


if __name__=='__main__': main()
