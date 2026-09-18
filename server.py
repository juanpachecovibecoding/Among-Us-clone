import socket
import selectors
import struct
import random
import pickle

# ---------------------------------------------------------------------------
# TCP framing helpers
# ---------------------------------------------------------------------------

def send_msg(sock, data):
    msg = struct.pack('>I', len(data)) + data
    try:
        sock.sendall(msg)
        return True
    except Exception:
        return False

def recv_exact(sock, n):
    buf = b''
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except Exception:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf

def recv_msg(sock):
    header = recv_exact(sock, 4)
    if header is None:
        return None
    length = struct.unpack('>I', header)[0]
    if length == 0 or length > 1_000_000:
        return None
    return recv_exact(sock, length)

# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------

print("Server Address: " + socket.gethostbyname(socket.gethostname()))

sel = selectors.DefaultSelector()
minionmap = {}
clients = {}

class Minion:
    def __init__(self, player_id):
        self.x = 50
        self.y = 50
        self.sync_img = None
        self.sync_img_index = None
        self.left_img_index = 0
        self.right_img_index = 0
        self.up_img_index = 0
        self.down_img_index = 0
        self.alive_status = True
        self.player_id = player_id
        self.player_colour = None
        self.tasks_completed = 0
        self.sabotagelights_sync = 0
        self.sabotagereactor_sync = 0
        self.victim_id = 0
        self.imposter = False
        self.emergency_sync = 0
        self.voted = None
        self.got_votes = 0
        self.emergency_meeting_img_sync = None
        self.emergency_meeting_img_sync_report = None
        self.victim_id_report = 0
        self.got_reported = False
        self.eject_sync = False
        self.eject_img = None

def updateWorld(message, sender_pid):
    try:
        arr = pickle.loads(message)
    except Exception as e:
        print(f'[WARN] pickle decode error from pid={sender_pid}: {e}')
        return
    player_id = arr[1]
    if player_id == 0:
        return
    if player_id not in minionmap:
        return
    m = minionmap[player_id]
    m.x = arr[2]; m.y = arr[3]; m.alive_status = arr[4]
    m.sync_img = arr[5]; m.sync_img_index = arr[6]
    m.left_img_index = arr[7]; m.right_img_index = arr[8]
    m.up_img_index = arr[9]; m.down_img_index = arr[10]
    m.player_colour = arr[11]; m.tasks_completed = arr[12]
    m.sabotagelights_sync = arr[13]; m.sabotagereactor_sync = arr[14]
    m.victim_id = arr[15]; m.imposter = arr[16]; m.emergency_sync = arr[17]
    m.voted = arr[18]; m.got_votes = arr[19]
    m.emergency_meeting_img_sync = arr[20]
    m.emergency_meeting_img_sync_report = arr[21]
    m.victim_id_report = arr[22]; m.got_reported = arr[23]
    m.eject_sync = arr[24]; m.eject_img = arr[25]
    update = ['player locations']
    for value in minionmap.values():
        update.append([
            value.player_id, value.x, value.y, value.alive_status,
            value.sync_img, value.sync_img_index,
            value.left_img_index, value.right_img_index,
            value.up_img_index, value.down_img_index,
            value.player_colour, value.tasks_completed,
            value.sabotagelights_sync, value.sabotagereactor_sync,
            value.victim_id, value.imposter, value.emergency_sync,
            value.voted, value.got_votes,
            value.emergency_meeting_img_sync,
            value.emergency_meeting_img_sync_report,
            value.victim_id_report, value.got_reported,
            value.eject_sync, value.eject_img
        ])
    payload = pickle.dumps(update)
    dead = []
    for fno, (sock, _, _) in list(clients.items()):
        if not send_msg(sock, payload):
            dead.append(fno)
    for fno in dead:
        _remove_client(fno)
    print('[INFO] sent world update')

def _remove_client(fno):
    if fno in clients:
        sock, _, pid = clients.pop(fno)
        try:
            sel.unregister(sock)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass
        if pid and pid in minionmap:
            del minionmap[pid]
        print(f'[INFO] client fd={fno} pid={pid} removed')

def accept(server_sock):
    conn, addr = server_sock.accept()
    conn.setblocking(False)
    player_id = random.randint(1000, 1_000_000)
    minionmap[player_id] = Minion(player_id)
    print(f'[INFO] Connection from {addr[0]}:{addr[1]} assigned pid={player_id}')
    conn.setblocking(True)
    send_msg(conn, pickle.dumps(['id update', player_id]))
    conn.setblocking(False)
    clients[conn.fileno()] = (conn, b'', player_id)
    sel.register(conn, selectors.EVENT_READ, data='client')

def read_client(fno):
    if fno not in clients:
        return
    sock, buf, pid = clients[fno]
    sock.setblocking(True)
    data = recv_msg(sock)
    sock.setblocking(False)
    if data is None:
        print(f'[INFO] client fd={fno} pid={pid} disconnected')
        _remove_client(fno)
        return
    updateWorld(data, pid)

server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server_sock.bind(('', 4321))
server_sock.listen(10)
server_sock.setblocking(False)
sel.register(server_sock, selectors.EVENT_READ, data='server')
print('[INFO] Server listening on port 4321...')
try:
    while True:
        events = sel.select(timeout=1)
        for key, mask in events:
            if key.data == 'server':
                accept(server_sock)
            elif key.data == 'client':
                read_client(key.fd)
except KeyboardInterrupt:
    print('\n[INFO] Server shutting down.')
finally:
    sel.close()
    server_sock.close()
