"""Explicit disposable schemas on the separate integration-test project."""
import hashlib
import unittest
from pathlib import Path
from psycopg import sql
from backend import db, postgres


class IsolatedDatabase:
    def __init__(self, directory):
        self.directory=Path(directory)
        self.schema="test_"+hashlib.sha256(str(self.directory).encode()).hexdigest()[:24]

    def start(self):
        try:
            self.target=postgres.test_target()
        except ValueError as error:
            if "required" in str(error):
                raise unittest.SkipTest(str(error)) from None
            raise
        self.previous=(db.DATA,db._target,db._schema)
        with postgres.connection(self.target) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        db.DATA,db._target,db._schema=self.directory,self.target,self.schema
        return self

    def stop(self):
        db.DATA,db._target,db._schema=self.previous
        # Only this test's newly created schema is disposable. Neither the
        # working target nor public/another schema can reach this statement.
        if not self.schema.startswith("test_") or len(self.schema)!=29:
            raise ValueError("Refusing to remove an unexpected test schema.")
        with postgres.connection(self.target) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def __enter__(self):
        return self.start()

    def __exit__(self,*_):
        self.stop()


def verify_users():
    db.execute("INSERT INTO account_security(user_id,verified_at) SELECT id,%s FROM users ON CONFLICT(user_id) DO NOTHING",(db.now(),))
