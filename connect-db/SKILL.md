---
name: connect-db
description: 连接本地 MySQL 数据库并执行 SQL 操作。当用户想要查询数据库、查看表结构、插入/更新/删除数据、或者做任何数据库相关操作时，必须使用此 skill。即使用户只是说"查一下数据库"、"看看数据"、"查个表"，也要触发此 skill。
---

# connect-db

连接本地 MySQL 数据库，执行 SQL 查询和操作。

## 默认连接配置

| 参数 | 值 |
|------|-----|
| Host | 127.0.0.1 |
| Port | 3306 |
| User | root |
| Password | dqh12345 |
| Database | hw1 |

## 操作方式

优先使用 Bash 工具通过 `mysql` 命令行客户端执行 SQL，格式如下：

```bash
mysql -h 127.0.0.1 -P 3306 -u root -pdqh12345 hw1 -e "SQL语句"
```

注意：`-p` 和密码之间没有空格。

### 查看所有表

```bash
mysql -h 127.0.0.1 -P 3306 -u root -pdqh12345 hw1 -e "SHOW TABLES;"
```

### 查看表结构

```bash
mysql -h 127.0.0.1 -P 3306 -u root -pdqh12345 hw1 -e "DESCRIBE 表名;"
```

### 查询数据

```bash
mysql -h 127.0.0.1 -P 3306 -u root -pdqh12345 hw1 -e "SELECT * FROM 表名 LIMIT 20;"
```

### 执行写操作（INSERT / UPDATE / DELETE）

```bash
mysql -h 127.0.0.1 -P 3306 -u root -pdqh12345 hw1 -e "SQL写操作语句;"
```

## 工作流程

1. 理解用户想做什么（查询、写入、查看结构等）
2. 如果用户没有指定表名，先执行 `SHOW TABLES` 列出所有表，再按需操作
3. 构造合适的 SQL 语句
4. 用 Bash 工具执行，将结果清晰地展示给用户
5. 如果出错，检查连接参数或 SQL 语法，告知用户具体原因

## 注意事项

- 执行 DELETE 或 DROP 等危险操作前，先向用户确认
- 查询结果较多时，加上 `LIMIT` 避免输出过长
- 用户如果提供了不同的连接参数，以用户提供的为准
