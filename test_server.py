"""Integration checks use a temporary database; personal records are never touched."""
import json
import base64
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import server

class LedgerTests(unittest.TestCase):
    PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = server.DB_PATH
        server.DB_PATH = Path(self.temp.name) / 'test.sqlite3'
        server.initialize_database()
        self.http = server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.http.server_port}'
        self.entry = dict(title='テストの食費',amount=1200,type='expense',category='食費',date='2026-09-08',note='メモ')
    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        server.DB_PATH = self.previous
        self.temp.cleanup()
    def request(self,path='/api/transactions',method='GET',data=None,headers=None):
        request = Request(self.base+path,data=None if data is None else json.dumps(data).encode(),method=method,headers=headers or ({'Content-Type':'application/json'} if data is not None else {}))
        try: response = urlopen(request)
        except HTTPError as error: response = error
        with response:
            body=response.read()
            return response.status,body,response.headers
    def test_crud_and_persistence(self):
        status,body,_=self.request(method='POST',data=self.entry)
        self.assertEqual(status,201)
        item=json.loads(body)
        self.assertEqual(item['note'],'メモ')
        path=f"/api/transactions/{item['id']}"
        self.assertEqual(self.request(path,'PUT',{**self.entry,'amount':4500})[0],200)
        with server.connection() as db:
            self.assertEqual(db.execute('SELECT amount FROM transactions').fetchone()[0],4500)
        self.assertEqual(self.request(path,'DELETE')[0],204)
        self.assertEqual(json.loads(self.request()[1]),[])
        server.initialize_database()
        self.assertEqual(json.loads(self.request()[1]),[])
    def test_validation(self):
        for patch in [{'amount':0},{'amount':-1},{'amount':1.5},{'amount':True},{'amount':1000000000},{'title':'   '},{'title':'a'*101},{'category':''},{'note':'x'*501},{'date':'2026-02-30'},{'date':'2026-9-01'},{'date':'1899-01-01'},{'type':'invalid'}]:
            with self.subTest(patch=patch):
                self.assertEqual(self.request(method='POST',data={**self.entry,**patch})[0],400)
        self.assertEqual(json.loads(self.request()[1]),[])
    def test_image_upload_serve_preserve_and_remove(self):
        data_url='data:image/png;base64,'+base64.b64encode(self.PNG).decode()
        status,body,_=self.request(method='POST',data={**self.entry,'image':data_url})
        self.assertEqual(status,201)
        item=json.loads(body)
        self.assertTrue(item['hasImage'])
        self.assertEqual(item['imageUrl'],f"/api/transactions/{item['id']}/image")
        status,body,headers=self.request(item['imageUrl'])
        self.assertEqual(status,200)
        self.assertEqual(headers.get_content_type(),'image/png')
        self.assertEqual(body,self.PNG)
        path=f"/api/transactions/{item['id']}"
        updated=json.loads(self.request(path,'PUT',{**self.entry,'amount':2400})[1])
        self.assertTrue(updated['hasImage'])
        removed=json.loads(self.request(path,'PUT',{**self.entry,'image':None})[1])
        self.assertFalse(removed['hasImage'])
        self.assertEqual(self.request(item['imageUrl'])[0],404)
    def test_invalid_images(self):
        invalid=[
            'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yw=',
            'data:image/png;base64,'+base64.b64encode(b'not a png').decode(),
            'data:image/png;base64,***',
        ]
        for image in invalid:
            with self.subTest(image=image[:30]):
                self.assertEqual(self.request(method='POST',data={**self.entry,'image':image})[0],400)
        self.assertEqual(json.loads(self.request()[1]),[])
    def test_missing_record(self):
        self.assertEqual(self.request('/api/transactions/999','PUT',self.entry)[0],404)
        self.assertEqual(self.request('/api/transactions/999','DELETE')[0],404)
    def test_backup_snapshot(self):
        data_url='data:image/png;base64,'+base64.b64encode(self.PNG).decode()
        self.request(method='POST',data={**self.entry,'image':data_url})
        status,body,headers=self.request('/api/backup')
        self.assertEqual(status,200)
        self.assertIn('attachment',headers['Content-Disposition'])
        path=Path(self.temp.name)/'snapshot.sqlite3'
        path.write_bytes(body)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            row=db.execute('SELECT note,image,image_mime FROM transactions').fetchone()
            self.assertEqual(row[0],'メモ')
            self.assertEqual(row[1],self.PNG)
            self.assertEqual(row[2],'image/png')
    def test_private_files_not_served(self):
        for path in ['/kakeibo.sqlite3','/server.py','/.backup/index.html','/../server.py','/unknown']:
            self.assertEqual(self.request(path)[0],404)
        self.assertEqual(self.request('/')[0],200)
    def test_cross_origin_blocked(self):
        self.assertEqual(self.request(method='POST',data=self.entry,headers={'Content-Type':'application/json','Origin':'https://other.example'})[0],403)
        self.assertEqual(self.request(headers={'Host':'other.example'})[0],403)
    def test_invalid_json(self):
        request=Request(self.base+'/api/transactions',data=b'{bad',headers={'Content-Type':'application/json'})
        with self.assertRaises(HTTPError) as caught: urlopen(request)
        self.assertEqual(caught.exception.code,400)
        caught.exception.close()
    def test_old_database_migration(self):
        old=Path(self.temp.name)/'old.sqlite3'
        with closing(sqlite3.connect(old)) as db, db:
            db.execute('CREATE TABLE transactions (id INTEGER PRIMARY KEY,title TEXT,amount INTEGER,type TEXT,category TEXT,date TEXT)')
            db.execute("INSERT INTO transactions VALUES(1,'既存',500,'expense','食費','2026-08-31')")
        server.DB_PATH=old
        server.initialize_database()
        with server.connection() as db:
            row=dict(db.execute('SELECT * FROM transactions').fetchone())
        self.assertEqual(row['title'],'既存')
        self.assertEqual(row['note'],'')
        self.assertIsNone(row['image'])
        self.assertIsNone(row['image_mime'])
        self.assertEqual(len(list(Path(self.temp.name).glob('old.before-migration-*.sqlite3'))),1)

if __name__=='__main__': unittest.main(verbosity=2)
