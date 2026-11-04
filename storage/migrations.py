"""Additive schema upgrade guard, with a consistent SQLite backup before writes."""
import sqlite3,uuid

SCHEMA_VERSION=2

def backup_before_upgrade(connection,path):
    version=connection.execute('PRAGMA user_version').fetchone()[0]
    if version>SCHEMA_VERSION:raise RuntimeError('数据库来自较新版本，请使用对应程序或升级前备份')
    existing=connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone()
    if version>=SCHEMA_VERSION or not existing:return None
    backup=path.with_name(path.stem+'.pre-v2-'+uuid.uuid4().hex[:8]+'.db')
    destination=sqlite3.connect(backup)
    try:connection.backup(destination)
    finally:destination.close()
    return backup
