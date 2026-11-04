import sqlite3,tempfile,unittest
from pathlib import Path
from storage.database import Database
from core.models import normalize_sample

class ReleaseSafetyTests(unittest.TestCase):
    def test_existing_database_gets_consistent_backup_before_schema_upgrade(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            path=Path(tmp)/'metrics.db'
            with sqlite3.connect(path) as old:
                old.execute('CREATE TABLE preserved(value TEXT)');old.execute("INSERT INTO preserved VALUES ('user-data')")
            old.close()
            db=Database(path)
            try:
                backups=list(path.parent.glob('metrics.pre-v2-*.db'))
                self.assertEqual(len(backups),1)
                with sqlite3.connect(backups[0]) as backup:self.assertEqual(backup.execute('SELECT value FROM preserved').fetchone()[0],'user-data')
                backup.close()
                self.assertEqual(db.conn.execute('PRAGMA user_version').fetchone()[0],2)
            finally:db.close()
    def test_invalid_numeric_sample_has_no_nonfinite_wire_value(self):
        sample=normalize_sample({'ts':1000,'type':'ping','host':'a','avg':float('inf')})
        self.assertEqual(sample['state'],'local_error')
        self.assertIsNone(sample['avg'])
    def test_newer_schema_is_not_downgraded(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            path=Path(tmp)/'newer.db';old=sqlite3.connect(path);old.execute('PRAGMA user_version=99');old.close();db=None
            try:
                with self.assertRaises(RuntimeError):db=Database(path)
            finally:
                if db:db.close()
    def test_diagnostic_metadata_labels_legacy_bucket_as_approximate(self):
        import time
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            db=Database(Path(tmp)/'legacy.db')
            try:
                minute=int(time.time()//60)*60-60
                db.flush_minute(minute,{'ping':{'a':{'avg':10,'min':10,'max':10,'loss':0,'jitter':0,'std':0}}})
                payload=db.history_payload('ping',1,'a')
                self.assertTrue(payload['metadata'][0]['approximate'])
                self.assertEqual(payload['metadata'][0]['resolution'],'minute')
            finally:db.close()
