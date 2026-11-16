# Проксирование на бэкенд — общее для всех location с /api.
proxy_pass http://backend:8000;
proxy_http_version 1.1;
proxy_set_header Host $host;
# После real_ip в $remote_addr — настоящий адрес клиента. Присланный клиентом X-Forwarded-For
# дальше не передаём: uvicorn берёт из него самый левый адрес, и его можно было бы подделать.
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
