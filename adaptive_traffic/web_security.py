"""Bounded ASGI request handling. Never trust client X-Forwarded-* directly."""
from collections import OrderedDict
import asyncio
import time

from starlette.responses import JSONResponse


class PublicPolicy:
    body_timeout = 5.0

    def __init__(self, app, config):
        self.app, self.config = app, config
        self.clients = OrderedDict()
        self.active_mutations = 0
        self.global_bucket = [config.global_per_minute, time.monotonic()]

    def take(self, bucket, capacity, now, cost):
        bucket[0]=min(capacity,bucket[0]+(now-bucket[1])*capacity/60)
        bucket[1]=now
        if bucket[0]<cost: return False
        bucket[0]-=cost
        return True

    async def __call__(self, scope, receive, send):
        if scope['type']!='http': return await self.app(scope,receive,send)
        headers={k.decode('latin-1').lower():v.decode('latin-1') for k,v in scope['headers']}
        path=scope['path']; mutating=scope['method'] not in ('GET','HEAD','OPTIONS')
        async def reject(code, detail):
            await JSONResponse({'detail':detail},status_code=code)(scope,receive,send)
        if mutating:
            origin=headers.get('origin')
            expected=f"{scope['scheme']}://{headers.get('host')}"
            allowed=self.config.origins or (expected,)
            if (origin and origin not in allowed) or (self.config.production and not origin):
                return await reject(403,'Запрос должен поступить с разрешённой страницы сайта')
        if self.config.production and path.startswith('/api/'):
            now=time.monotonic(); ip=(scope.get('client') or ('unknown',0))[0]
            # IP comes from ASGI; Uvicorn accepts proxy headers only from configured proxies.
            bucket=self.clients.pop(ip,[self.config.requests_per_minute,now]);self.clients[ip]=bucket
            while len(self.clients)>1024: self.clients.popitem(last=False)
            cost=30 if mutating and path in ('/api/sessions','/api/comparison') else 1
            if path.endswith('/finish'): cost=60
            if not self.take(bucket,self.config.requests_per_minute,now,cost) or not self.take(self.global_bucket,self.config.global_per_minute,now,cost):
                return await reject(429,'Лимит запросов. Подождите и повторите.')
        if mutating:
            body=bytearray()
            async def read_body():
                while True:
                    message=await receive()
                    if message['type']=='http.disconnect': return 'disconnected'
                    body.extend(message.get('body',b''))
                    if len(body)>16384: return 'too_large'
                    if not message.get('more_body'): return 'complete'
            try:
                # Total deadline, including chunked bodies; keep-alive timeout only
                # applies between requests and cannot stop a stalled upload.
                status=await asyncio.wait_for(read_body(),timeout=self.body_timeout)
            except asyncio.TimeoutError:
                return await reject(408,'Время передачи запроса истекло')
            if status=='disconnected': return
            if status=='too_large': return await reject(413,'Максимальный размер запроса — 16 КиБ')
            delivered=False
            original_receive=receive
            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered=True
                    return {'type':'http.request','body':bytes(body),'more_body':False}
                return await original_receive()
            receive=replay
        async def secure_send(message):
            if message['type']=='http.response.start':
                message['headers']+= [(b'x-content-type-options',b'nosniff'),
                    (b'referrer-policy',b'no-referrer'),(b'permissions-policy',b'serial=(self)'),
                    (b'content-security-policy',b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")]
                if path.startswith('/api/'): message['headers'].append((b'cache-control',b'no-store'))
            await send(message)
        limited=self.config.production and mutating and path.startswith('/api/')
        if limited:
            if self.active_mutations>=2:
                return await reject(503,'Сервер занят. Повторите запрос позже.')
            self.active_mutations+=1
        try:
            await self.app(scope,receive,secure_send)
        finally:
            if limited: self.active_mutations-=1
