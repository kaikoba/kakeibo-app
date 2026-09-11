"""tsumugi: local, single-user ledger. Python 3.12+, no dependencies."""
import argparse
import json
import re
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from datetime import date, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / 'kakeibo.sqlite3'
STATIC_FILES = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/styles.css': 'styles.css'}

@contextmanager
def connection():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()

def initialize_database():
    # Back up before migrating an existing database. Never seed personal records.
    if DB_PATH.exists():
        with connection() as db:
            columns = [row[1] for row in db.execute('PRAGMA table_info(transactions)')]
            if columns and 'note' not in columns:
                backup = DB_PATH.with_name(f'{DB_PATH.stem}.before-migration-{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3')
                with closing(sqlite3.connect(backup)) as target:
                    db.backup(target)
    with connection() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            amount INTEGER NOT NULL CHECK(amount > 0),
            type TEXT NOT NULL CHECK(type IN ('income','expense')),
            category TEXT NOT NULL,
            date TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT ''
        )''')
        if 'note' not in [row[1] for row in db.execute('PRAGMA table_info(transactions)')]:
            db.execute("ALTER TABLE transactions ADD COLUMN note TEXT NOT NULL DEFAULT ''")
        db.execute('CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date)')

def validate(data):
    if not isinstance(data, dict):
        raise ValueError('入力形式が正しくありません。')
    result = {}
    for key, label, limit in [('title','内容',100),('category','カテゴリ',40),('note','メモ',500)]:
        value = data.get(key, '' if key == 'note' else None)
        if not isinstance(value, str) or len(value.strip()) > limit or (key != 'note' and not value.strip()):
            raise ValueError(f'{label}は{limit}文字以内で入力してください。')
        result[key] = value.strip()
    amount = data.get('amount')
    if type(amount) is not int or not 1 <= amount <= 999999999:
        raise ValueError('金額は1〜999,999,999円の整数で入力してください。')
    if data.get('type') not in ('income','expense'):
        raise ValueError('収入または支出を選択してください。')
    value = data.get('date')
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('正しい日付を入力してください。')
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError('存在する日付を入力してください。') from None
    if parsed.year < 1900:
        raise ValueError('1900年以降の日付を入力してください。')
    result.update(amount=amount,type=data['type'],date=value)
    return result

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,directory=str(ROOT),**kwargs)

    def end_headers(self):
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        super().end_headers()

    def send_json(self,status,payload=None):
        body = b'' if status == 204 else json.dumps(payload,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def allowed(self, mutation=False):
        expected = {f'localhost:{self.server.server_port}',f'127.0.0.1:{self.server.server_port}'}
        host = self.headers.get('Host','')
        origin = self.headers.get('Origin')
        if host not in expected or (origin and origin != f'http://{host}'):
            self.send_json(403,{'error':'このパソコンのアプリから操作してください。'})
            return False
        if mutation and self.headers.get('Sec-Fetch-Site') == 'cross-site':
            self.send_json(403,{'error':'別のサイトからは操作できません。'})
            return False
        return True

    def read_json(self):
        if self.headers.get_content_type() != 'application/json':
            raise ValueError('JSON形式で送信してください。')
        length = int(self.headers.get('Content-Length','0'))
        if not 0 < length <= 16384:
            raise ValueError('入力データのサイズが正しくありません。')
        try:
            return json.loads(self.rfile.read(length))
        except (UnicodeDecodeError,json.JSONDecodeError):
            raise ValueError('入力データを読み取れませんでした。') from None

    def do_GET(self):
        if not self.allowed(): return
        path = urlparse(self.path).path
        try:
            if path == '/api/transactions':
                with connection() as db:
                    rows = db.execute('SELECT * FROM transactions ORDER BY date DESC,id DESC').fetchall()
                self.send_json(200,[dict(row) for row in rows])
            elif path == '/api/backup':
                # SQLite's backup API produces a consistent snapshot even during writes.
                with tempfile.TemporaryDirectory() as folder:
                    output = Path(folder) / 'backup.sqlite3'
                    with connection() as db, closing(sqlite3.connect(output)) as target:
                        db.backup(target)
                    body = output.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type','application/vnd.sqlite3')
                self.send_header('Content-Disposition',f'attachment; filename="tsumugi_{date.today().isoformat()}.sqlite3"')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path in STATIC_FILES:
                self.path = '/' + STATIC_FILES[path]
                super().do_GET()
            else:
                self.send_json(404,{'error':'ページが見つかりません。'})
        except sqlite3.Error:
            self.send_json(503,{'error':'データベースに接続できません。しばらくしてから再試行してください。'})

    def do_HEAD(self):
        if not self.allowed(): return
        path = urlparse(self.path).path
        if path not in STATIC_FILES:
            self.send_json(404,{'error':'ページが見つかりません。'})
            return
        self.path = '/' + STATIC_FILES[path]
        super().do_HEAD()

    def mutation(self,method):
        if not self.allowed(mutation=True): return
        path = urlparse(self.path).path
        match = re.fullmatch(r'/api/transactions/([1-9]\d{0,17})',path)
        if (method == 'POST' and path != '/api/transactions') or (method != 'POST' and not match):
            self.send_json(404,{'error':'記録が見つかりません。'})
            return
        try:
            item_id = int(match[1]) if match else None
            data = validate(self.read_json()) if method != 'DELETE' else None
            with connection() as db:
                if item_id is not None and not db.execute('SELECT id FROM transactions WHERE id=?',(item_id,)).fetchone():
                    self.send_json(404,{'error':'この記録はすでに削除されています。再読み込みしてください。'})
                    return
                if method == 'DELETE':
                    db.execute('DELETE FROM transactions WHERE id=?',(item_id,))
                    result = None
                else:
                    values = tuple(data[key] for key in ('title','amount','type','category','date','note'))
                    if method == 'POST':
                        item_id = db.execute('INSERT INTO transactions(title,amount,type,category,date,note) VALUES(?,?,?,?,?,?)',values).lastrowid
                    else:
                        db.execute('UPDATE transactions SET title=?,amount=?,type=?,category=?,date=?,note=? WHERE id=?',values+(item_id,))
                    result = dict(db.execute('SELECT * FROM transactions WHERE id=?',(item_id,)).fetchone())
            self.send_json(201 if method == 'POST' else 204 if method == 'DELETE' else 200,result)
        except (ValueError,TypeError) as error:
            self.send_json(400,{'error':str(error)})
        except sqlite3.Error:
            self.send_json(503,{'error':'保存できませんでした。しばらくしてから再試行してください。'})

    def do_POST(self): self.mutation('POST')
    def do_PUT(self): self.mutation('PUT')
    def do_DELETE(self): self.mutation('DELETE')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='tsumugi local ledger')
    parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--db',type=Path,default=DB_PATH)
    args = parser.parse_args()
    DB_PATH = args.db.resolve()
    initialize_database()
    server = ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'tsumugi is running at http://localhost:{args.port}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
