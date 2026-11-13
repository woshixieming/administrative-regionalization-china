-- 可选：数据库管理员先创建 divisions_app 登录角色并设置密码，再执行本文件。
-- 导入使用数据库所有者账号；运行后端可使用只读账号。
GRANT USAGE ON SCHEMA divisions TO divisions_app;
GRANT SELECT ON ALL TABLES IN SCHEMA divisions TO divisions_app;
-- 新版本使用既有表，不需要运行账号拥有建表或写入权限。
