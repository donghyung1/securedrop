#!/bin/bash
set -euo pipefail
dnf install -y python3.12 python3.12-pip nginx
useradd --system --home-dir /opt/securedrop --shell /sbin/nologin securedrop
install -d -o securedrop -g securedrop /opt/securedrop /var/lib/securedrop
aws s3 cp 's3://__BUCKET__/__ARTIFACT__' /tmp/securedrop.tar.gz --region __REGION__ --only-show-errors
tar xzf /tmp/securedrop.tar.gz -C /opt/securedrop
rm /tmp/securedrop.tar.gz
python3.12 -m venv /opt/securedrop/.venv
/opt/securedrop/.venv/bin/pip install --require-hashes -r /opt/securedrop/requirements.txt
umask 077
cat > /opt/securedrop/.env <<'EOF'
AWS_REGION=__REGION__
S3_BUCKET=__BUCKET__
DATABASE_PATH=/var/lib/securedrop/securedrop.db
ALLOWED_ORIGINS=[]
EOF
printf 'PLUGIN_API_KEY=%s\n' "$(python3.12 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> /opt/securedrop/.env
chown -R securedrop:securedrop /opt/securedrop /var/lib/securedrop
cat > /etc/systemd/system/securedrop.service <<'EOF'
[Unit]
Description=SecureDrop FastAPI
After=network-online.target
Wants=network-online.target
[Service]
User=securedrop
Group=securedrop
WorkingDirectory=/opt/securedrop
ExecStart=/opt/securedrop/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/securedrop
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/nginx/nginx.conf <<'EOF'
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log crit;
pid /run/nginx.pid;
events { worker_connections 1024; }
http {
    include /etc/nginx/mime.types;
    access_log off;
    server_tokens off;
    server {
        listen 80 default_server;
        if ($http_x_origin_verify != "__ORIGIN_SECRET__") { return 403; }
        client_max_body_size 21m;
        client_body_buffer_size 21m;
        client_body_timeout 60s;
        add_header X-Content-Type-Options nosniff always;
        add_header Referrer-Policy no-referrer always;
        add_header Strict-Transport-Security "max-age=31536000" always;
        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Forwarded-Proto https;
            proxy_set_header X-Forwarded-For $remote_addr;
            proxy_set_header X-Origin-Verify "";
            proxy_request_buffering off;
            proxy_buffering off;
            proxy_max_temp_file_size 0;
            proxy_read_timeout 60s;
        }
    }
}
EOF
chmod 644 /etc/nginx/nginx.conf /etc/systemd/system/securedrop.service
systemctl daemon-reload
systemctl enable --now securedrop nginx
touch /opt/securedrop/.bootstrap-ready
