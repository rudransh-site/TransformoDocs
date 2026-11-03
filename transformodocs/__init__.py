"""TransformoDocs project package.

MySQL driver selection: prefer mysqlclient; fall back to PyMySQL (pure Python,
installs everywhere, same Django behaviour). No effect in SQLite dev mode.
"""
try:
    import MySQLdb  # noqa: F401  (mysqlclient)
except ImportError:
    try:
        import pymysql

        # Django checks the driver version; PyMySQL must pose as a recent mysqlclient.
        pymysql.version_info = (2, 2, 1, "final", 0)
        pymysql.install_as_MySQLdb()
    except ImportError:
        pass  # Django will raise a clear "no MySQL driver" error if MySQL is used.
