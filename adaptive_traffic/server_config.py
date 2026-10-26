"""Deployment policy, independent of scientific model parameters."""
from dataclasses import dataclass, field
import os
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ServerConfig:
    production: bool = False
    hosts: tuple[str, ...] = ('127.0.0.1', 'localhost')
    origins: tuple[str, ...] = ()
    admin_token: str = field(default='', repr=False)
    session_capacity: int = 32
    session_ttl: int = 1800
    max_duration: int = 3600
    requests_per_minute: int = 600
    global_per_minute: int = 1800
    lab_timeout: int = 120

    @classmethod
    def from_env(cls):
        environment = os.getenv('TRAFFIC_ENV', 'local')
        if environment not in ('local','production'):
            raise ValueError('TRAFFIC_ENV: local или production')
        production = environment == 'production'
        hosts = tuple(x.strip() for x in os.getenv('ALLOWED_HOSTS','127.0.0.1,localhost').split(',') if x.strip())
        origins = tuple(x.strip().rstrip('/') for x in os.getenv('ALLOWED_ORIGINS','').split(',') if x.strip())
        token = os.getenv('ADMIN_TOKEN','')
        if not hosts or any('*' in h or '/' in h or ':' in h for h in hosts):
            raise ValueError('ALLOWED_HOSTS: укажите точные имена хостов без схемы, порта и wildcard')
        for origin in origins:
            url=urlsplit(origin)
            if url.scheme not in ('http','https') or url.hostname not in hosts or url.path or url.query or url.fragment or url.username:
                raise ValueError('ALLOWED_ORIGINS: нужны точные origins разрешённых хостов')
            if production and url.scheme!='https':
                raise ValueError('Production требует HTTPS origins')
        if production and not origins:
            raise ValueError('Production требует явные ALLOWED_HOSTS и ALLOWED_ORIGINS')
        if token and len(token)<32:
            raise ValueError('ADMIN_TOKEN должен содержать не менее 32 символов')
        def bounded(name, default, low, high):
            value=int(os.getenv(name,str(default)))
            if not low<=value<=high: raise ValueError(f'{name}: допустимо {low}..{high}')
            return value
        return cls(production=production,hosts=hosts,origins=origins,admin_token=token,
                   session_capacity=bounded('MAX_SESSIONS',8 if production else 32,1,32),
                   session_ttl=bounded('SESSION_TTL',900 if production else 1800,60,1800),
                   max_duration=bounded('MAX_DURATION',600 if production else 3600,60,3600))
