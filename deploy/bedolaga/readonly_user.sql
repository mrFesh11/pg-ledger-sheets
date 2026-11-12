\set ON_ERROR_STOP on
select 'create role ledger_ro login' where not exists (select from pg_roles where rolname = 'ledger_ro') \gexec
alter role ledger_ro password :'password';
alter role ledger_ro set default_transaction_read_only = on;
grant connect on database :"dbname" to ledger_ro;
grant usage on schema public to ledger_ro;
grant select (id, user_id, type, amount_kopeks, description, payment_method, is_completed, created_at, completed_at) on transactions to ledger_ro;
grant select (id, telegram_id, username, email) on users to ledger_ro;
