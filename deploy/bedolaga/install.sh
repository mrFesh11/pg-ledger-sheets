#!/usr/bin/env bash
set -euo pipefail
sheet_id=${1:?usage: install.sh <sheet_id> <docker_network> [db_container] [db_user] [db_name]}
network=${2:?docker network of the bot}
db_container=${3:-remnawave_bot_db}
db_user=${4:-remnawave_user}
db_name=${5:-remnawave_bot}
cd "$(dirname "$0")/../.."
etc=/etc/pg-ledger-sheets
install -d -m 700 $etc
install -d -o 10001 -g 10001 -m 700 /var/lib/pg-ledger-sheets
chown 10001:10001 $etc/service-account.json
chmod 400 $etc/service-account.json
docker build -q -t pg-ledger-sheets .
if [ ! -f $etc/env ]; then
    password=$(openssl rand -hex 24)
    (
        umask 077
        sed -e "s/CHANGE_ME/$password/" -e "s/^LEDGER_SHEET_ID=.*/LEDGER_SHEET_ID=$sheet_id/" -e "s/^LEDGER_NETWORK=.*/LEDGER_NETWORK=$network/" \
            -e "s|/remnawave_bot$|/$db_name|" deploy/env.example > $etc/env
    )
else
    echo "$etc/env уже есть: аргументы не применены, правьте файл вручную"
fi
password=$(sed -n 's|^LEDGER_DSN=postgresql://ledger_ro:\([^@]*\)@.*|\1|p' $etc/env)
{
    printf '\\set password %s\n\\set dbname %s\n' "$password" "$db_name"
    cat deploy/bedolaga/readonly_user.sql
} | docker exec -i "$db_container" psql -q -U "$db_user" -d "$db_name"
install -m 644 deploy/pg-ledger-sheets.service deploy/pg-ledger-sheets.timer /etc/systemd/system/
systemctl daemon-reload
echo "установлено; дальше init и systemctl enable --now pg-ledger-sheets.timer (см. README)"
