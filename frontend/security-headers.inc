# Заголовки безопасности. add_header внутри location отменяет наследование от server,
# поэтому этот файл подключается в каждый location (include), а не задаётся один раз.
add_header X-Content-Type-Options nosniff always;
add_header X-Frame-Options DENY always;
add_header Referrer-Policy same-origin always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=()" always;
add_header Content-Security-Policy "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'" always;
# HSTS ставит внешний прокси, который терминирует HTTPS (здесь nginx слушает HTTP).
