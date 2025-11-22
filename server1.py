import asyncio
import json
import sqlite3
import os
import ssl
import bcrypt #type: ignore
from cryptography.fernet import Fernet #type: ignore
from datetime import datetime

DB_FILE = 'chat_server.db'
HOST = '0.0.0.0'
PORT = 8765
TLS_CERT = 'cert.pem'
TLS_KEY = 'key.pem'
CHUNK_SIZE = 64 * 1024

# --- Database helpers ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash BLOB, created_at DATETIME
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY, room TEXT, sender TEXT, text TEXT, ts DATETIME
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY, room TEXT, sender TEXT, filename TEXT, path TEXT, ts DATETIME
        )
    ''')
    conn.commit()
    conn.close()

# --- Simple in-memory server state ---
clients = {}  # writer -> {username, room, fernet (optional)}
rooms = {}    # room -> set of writers

# --- Helper functions ---
async def send_json(writer, obj):
    data = json.dumps(obj, separators=(',', ':')).encode('utf-8') + b"\n"
    writer.write(data)
    await writer.drain()

async def broadcast(room, obj, exclude_writer=None):
    if room not in rooms:
        return
    data = json.dumps(obj, separators=(',', ':')).encode('utf-8') + b"\n"
    for w in list(rooms[room]):
        if w is exclude_writer:
            continue
        try:
            w.write(data)
            await w.drain()
        except Exception:
            await unregister(w)

# --- Authentication ---
def register_user(username, password):
    pw_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute('INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)',
                  (username, pw_hash, datetime.utcnow()))
        conn.commit()
        return True, 'ok'
    except sqlite3.IntegrityError:
        return False, 'username_taken'
    finally:
        conn.close()

def verify_user(username, password):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT password_hash FROM users WHERE username=?', (username,))
    row = c.fetchone()
    conn.close()
    if not row:
        return False
    pw_hash = row[0]
    try:
        return bcrypt.checkpw(password.encode('utf-8'), pw_hash)
    except Exception:
        return False

# --- Message persistence ---
def store_message(room, sender, text):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT INTO messages (room, sender, text, ts) VALUES (?, ?, ?, ?)',
              (room, sender, text, datetime.utcnow()))
    conn.commit()
    conn.close()

def get_recent_messages(room, limit=100):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT sender, text, ts FROM messages WHERE room=? ORDER BY id DESC LIMIT ?', (room, limit))
    rows = c.fetchall()
    conn.close()
    return [{'sender': r[0], 'text': r[1], 'ts': r[2]} for r in reversed(rows)]

# --- File storage ---
FILES_DIR = 'uploads'
os.makedirs(FILES_DIR, exist_ok=True)

async def handle_file_transfer(meta, reader, writer):
    # meta: {filename, size, room, sender}
    filename = os.path.basename(meta['filename'])
    size = int(meta['size'])
    room = meta['room']
    sender = meta['sender']
    ts = datetime.utcnow().isoformat()
    out_path = os.path.join(FILES_DIR, f"{int(datetime.utcnow().timestamp())}_{filename}")
    with open(out_path, 'wb') as f:
        remaining = size
        while remaining > 0:
            chunk = await reader.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            f.write(chunk)
            remaining -= len(chunk)
    # persist file meta
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT INTO files (room, sender, filename, path, ts) VALUES (?, ?, ?, ?, ?)',
              (room, sender, filename, out_path, ts))
    conn.commit()
    conn.close()
    # notify room
    await broadcast(room, {'type': 'file_shared', 'room': room, 'sender': sender, 'filename': filename, 'path': out_path, 'ts': ts})

# --- Register / unregister clients ---
async def register(writer, username):
    clients[writer] = {'username': username, 'room': None, 'fernet': None}

async def unregister(writer):
    info = clients.get(writer)
    if not info:
        return
    room = info.get('room')
    username = info.get('username')
    if room and writer in rooms.get(room, set()):
        rooms[room].remove(writer)
        await broadcast(room, {'type': 'system', 'text': f"{username} left the room"})
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass
    clients.pop(writer, None)

# --- Main client handler ---
async def handle_client(reader, writer):
    peer = writer.get_extra_info('peername')
    print('Client connected:', peer)
    try:
        # We'll read line-delimited JSON messages
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                msg = json.loads(line.decode('utf-8'))
            except Exception as e:
                await send_json(writer, {'type': 'error', 'reason': 'invalid_json'})
                continue
            mtype = msg.get('type')
            if mtype == 'register':
                ok, reason = register_user(msg['username'], msg['password'])
                await send_json(writer, {'type': 'register_response', 'ok': ok, 'reason': reason})
            elif mtype == 'login':
                if verify_user(msg['username'], msg['password']):
                    await register(writer, msg['username'])
                    await send_json(writer, {'type': 'login_response', 'ok': True})
                else:
                    await send_json(writer, {'type': 'login_response', 'ok': False, 'reason': 'bad_credentials'})
            else:
                # other message types require authenticated client
                if writer not in clients:
                    await send_json(writer, {'type': 'error', 'reason': 'not_authenticated'})
                    continue
                info = clients[writer]
                username = info['username']
                if mtype == 'join':
                    room = msg.get('room', 'main')
                    # leave old room
                    old = info.get('room')
                    if old and writer in rooms.get(old, set()):
                        rooms[old].remove(writer)
                        await broadcast(old, {'type':'system', 'text': f"{username} left the room"})
                    info['room'] = room
                    rooms.setdefault(room, set()).add(writer)
                    await send_json(writer, {'type': 'join_response', 'ok': True, 'room': room})
                    # send recent history
                    history = get_recent_messages(room)
                    await send_json(writer, {'type': 'history', 'room': room, 'messages': history})
                    await broadcast(room, {'type': 'system', 'text': f"{username} joined the room"}, exclude_writer=writer)
                elif mtype == 'message':
                    room = info.get('room') or 'main'
                    text = msg.get('text', '')
                    # optional: decrypt if client sent encrypted payload; demo omitted
                    store_message(room, username, text)
                    await broadcast(room, {'type': 'message', 'room': room, 'sender': username, 'text': text, 'ts': datetime.utcnow().isoformat()})
                elif mtype == 'file_meta':
                    # client intends to send raw file bytes next; server will read exact size
                    meta = msg.get('meta')
                    # meta must include filename, size
                    await send_json(writer, {'type': 'file_ready'})
                    await handle_file_transfer({'filename': meta['filename'], 'size': meta['size'], 'room': info.get('room','main'), 'sender': username}, reader, writer)
                elif mtype == 'list_rooms':
                    await send_json(writer, {'type': 'rooms', 'rooms': list(rooms.keys())})
                else:
                    await send_json(writer, {'type': 'error', 'reason': 'unknown_type'})
    except Exception as e:
        print('Connection error:', e)
    finally:
        await unregister(writer)
        print('Client disconnected:', peer)

# --- TLS context (self-signed allowed for testing) ---
def make_ssl_context():
    if not os.path.exists(TLS_CERT) or not os.path.exists(TLS_KEY):
        print('TLS cert or key not found. Starting without TLS (development only).')
        return None
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(certfile=TLS_CERT, keyfile=TLS_KEY)
    return context

async def main_server():
    init_db()
    sslctx = make_ssl_context()
    server = await asyncio.start_server(handle_client, HOST, PORT, ssl=sslctx)
    addr = server.sockets[0].getsockname()
    print(f'Serving on {addr}')
    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    try:
        asyncio.run(main_server())
    except KeyboardInterrupt:
        print('Server stopped')
