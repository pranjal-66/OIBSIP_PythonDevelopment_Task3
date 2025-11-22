import socket
import ssl
import threading
import json
import tkinter as tk
from tkinter import simpledialog, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
import os
from datetime import datetime
from plyer import notification   #type: ignore

SERVER_HOST = '127.0.0.1'
SERVER_PORT = 8765
USE_TLS = False
CHUNK_SIZE = 64 * 1024


# ============================================================
#                     CHAT CLIENT GUI
# ============================================================

class ChatClientGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Advanced Chat Client")
        self.root.geometry("820x600")

        # theme colors
        self.light_theme = {
            "bg": "#f0f0f0",
            "text_bg": "#ffffff",
            "bubble_me": "#c8e6c9",
            "bubble_other": "#e3f2fd",
            "text_color": "black",
            "btn_bg": "#4caf50",
            "btn_fg": "white"
        }

        self.dark_theme = {
            "bg": "#303030",
            "text_bg": "#424242",
            "bubble_me": "#66bb6a",
            "bubble_other": "#42a5f5",
            "text_color": "white",
            "btn_bg": "#81c784",
            "btn_fg": "black"
        }

        self.theme = self.light_theme

        self.sock = None
        self.reader = None
        self.writer = None
        self.username = None
        self.current_room = None

        self.build_ui()
        self.apply_theme()

    # -----------------------------------------------------------
    # UI Construction
    # -----------------------------------------------------------
    def build_ui(self):
        top = tk.Frame(self.root)
        top.pack(fill='x')

        # login button
        self.login_btn = tk.Button(top, text="Login/Register", command=self.login_flow, width=15)
        self.login_btn.pack(side='left', padx=10, pady=10)

        self.room_entry = tk.Entry(top, width=20)
        self.room_entry.pack(side='left', padx=5)

        self.join_btn = tk.Button(top, text="Join Room", command=self.join_room, width=12)
        self.join_btn.pack(side='left', padx=5)

        self.rooms_btn = tk.Button(top, text="Rooms", command=self.list_rooms, width=10)
        self.rooms_btn.pack(side='left', padx=5)

        self.theme_btn = tk.Button(top, text="Toggle Theme", command=self.toggle_theme, width=12)
        self.theme_btn.pack(side='left', padx=5)

        # chat display
        self.display = ScrolledText(self.root, state='disabled', font=("Segoe UI", 11), wrap="word")
        self.display.pack(fill='both', expand=True, padx=10, pady=10)

        bottom = tk.Frame(self.root)
        bottom.pack(fill='x')

        self.entry = tk.Entry(bottom, font=("Segoe UI", 12))
        self.entry.pack(side='left', fill='x', expand=True, padx=10, pady=10)

        self.send_btn = tk.Button(bottom, text="Send", command=self.send_message, width=10)
        self.send_btn.pack(side='left', padx=5)

        self.file_btn = tk.Button(bottom, text="Send File", command=self.send_file, width=10)
        self.file_btn.pack(side='left', padx=5)

        self.status = tk.Label(self.root, text="Disconnected", anchor='w')
        self.status.pack(fill='x')

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # configure message bubble tags
        self.display.tag_configure("bubble_me", background=self.theme["bubble_me"], foreground=self.theme["text_color"],
                                   lmargin1=50, lmargin2=50, rmargin=10, spacing3=5, wrap="word")
        self.display.tag_configure("bubble_other", background=self.theme["bubble_other"],
                                   foreground=self.theme["text_color"],
                                   lmargin1=10, lmargin2=10, rmargin=50, spacing3=5, wrap="word")

    # -----------------------------------------------------------
    # Theme Handling
    # -----------------------------------------------------------
    def apply_theme(self):
        t = self.theme

        self.root.configure(bg=t["bg"])
        for widget in self.root.winfo_children():
            try:
                widget.configure(bg=t["bg"], fg=t["text_color"])
            except:
                pass

        self.display.configure(background=t["text_bg"], foreground=t["text_color"])

        # recolor bubble tags
        self.display.tag_configure("bubble_me", background=t["bubble_me"], foreground=t["text_color"])
        self.display.tag_configure("bubble_other", background=t["bubble_other"], foreground=t["text_color"])

        # buttons
        for btn in [self.login_btn, self.join_btn, self.rooms_btn, self.theme_btn,
                    self.send_btn, self.file_btn]:
            btn.configure(bg=t["btn_bg"], fg=t["btn_fg"], activebackground="#dddddd")

    def toggle_theme(self):
        self.theme = self.dark_theme if self.theme == self.light_theme else self.light_theme
        self.apply_theme()

    # -----------------------------------------------------------
    # Networking
    # -----------------------------------------------------------
    def connect_socket(self):
        if self.sock:
            return True
        try:
            raw = socket.create_connection((SERVER_HOST, SERVER_PORT))
            if USE_TLS:
                ctx = ssl.create_default_context()
                self.sock = ctx.wrap_socket(raw, server_hostname=SERVER_HOST)
            else:
                self.sock = raw
            self.reader = self.sock.makefile("rb")
            self.writer = self.sock
            threading.Thread(target=self.listen_loop, daemon=True).start()
            self.status.configure(text="Connected")
            return True
        except Exception as e:
            messagebox.showerror("Connection failed", str(e))
            return False

    # -----------------------------------------------------------
    # Notifications
    # -----------------------------------------------------------
    def notify(self, title, message):
        try:
            notification.notify(title=title, message=message, timeout=3)
        except:
            pass

    # -----------------------------------------------------------
    # Message Bubble
    # -----------------------------------------------------------
    def add_bubble(self, sender, text, mine=False):
        timestamp = datetime.now().strftime("%H:%M")

        bubble_text = f"{sender}  ({timestamp})\n{text}\n"

        self.display.configure(state='normal')
        tag = "bubble_me" if mine else "bubble_other"
        self.display.insert("end", bubble_text, tag)
        self.display.insert("end", "\n")
        self.display.see("end")
        self.display.configure(state='disabled')

    # -----------------------------------------------------------
    # Login & User Actions
    # -----------------------------------------------------------
    def login_flow(self):
        if not self.connect_socket():
            return

        choice = messagebox.askquestion("Register", "Do you want to register?")
        username = simpledialog.askstring("Username", "Enter username:")
        if not username:
            return
        password = simpledialog.askstring("Password", "Enter password:", show="*")
        if not password:
            return

        if choice == "yes":
            self.send_json({"type": "register", "username": username, "password": password})
        else:
            self.send_json({"type": "login", "username": username, "password": password})
        self.attempted_username = username

    def join_room(self):
        if not self.username:
            messagebox.showinfo("Not logged in", "Please login first")
            return
        room = self.room_entry.get().strip() or "main"
        self.send_json({"type": "join", "room": room})

    def list_rooms(self):
        if not self.username:
            messagebox.showinfo("Not logged in", "Login first")
            return
        self.send_json({"type": "list_rooms"})

    def send_message(self):
        text = self.entry.get().strip()
        if not text:
            return

        text = text.replace(":smile:", "😄").replace(":heart:", "❤️")
        self.send_json({"type": "message", "text": text})
        self.entry.delete(0, 'end')

        self.add_bubble(self.username, text, mine=True)

    def send_file(self):
        if not self.username:
            messagebox.showinfo("Not logged in", "Login first")
            return

        filepath = filedialog.askopenfilename()
        if not filepath:
            return

        size = os.path.getsize(filepath)
        filename = os.path.basename(filepath)
        self.send_json({"type": "file_meta", "meta": {"filename": filename, "size": size}})

        try:
            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    self.writer.sendall(chunk)

            self.add_bubble("You", f"Sent file: {filename}", mine=True)
        except Exception as e:
            messagebox.showerror("File send failed", str(e))

    # -----------------------------------------------------------
    # JSON Send
    # -----------------------------------------------------------
    def send_json(self, obj):
        try:
            data = json.dumps(obj).encode("utf-8") + b"\n"
            self.writer.sendall(data)
        except Exception as e:
            messagebox.showerror("Send error", str(e))

    # -----------------------------------------------------------
    # Server Listener
    # -----------------------------------------------------------
    def listen_loop(self):
        try:
            while True:
                line = self.reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8"))
                    self.handle_server_message(msg)
                except:
                    continue
        except:
            pass
        finally:
            if self.sock:
                self.sock.close()

    # -----------------------------------------------------------
    # Server Message Processing
    # -----------------------------------------------------------
    def handle_server_message(self, msg):
        mtype = msg.get("type")

        if mtype == "register_response":
            if msg.get("ok"):
                messagebox.showinfo("Registered", "Registration successful.")
            else:
                messagebox.showerror("Failed", "Registration failed")

        elif mtype == "login_response":
            if msg.get("ok"):
                self.username = self.attempted_username
                messagebox.showinfo("Login", "Login successful")
                self.status.configure(text=f"Logged in as {self.username}")
            else:
                messagebox.showerror("Login failed", "Bad credentials")

        elif mtype == "join_response":
            room = msg.get("room")
            self.current_room = room
            self.add_bubble("System", f"Joined room: {room}")

        elif mtype == "history":
            for m in msg.get("messages", []):
                self.add_bubble(m["sender"], m["text"])

        elif mtype == "message":
            sender = msg["sender"]
            text = msg["text"]
            if sender == self.username:
                return

            self.add_bubble(sender, text, mine=False)

            if self.root.state() == "iconic":
                self.notify(f"New message from {sender}", text)

        elif mtype == "system":
            self.add_bubble("System", msg.get("text"))

        elif mtype == "file_shared":
            sender = msg["sender"]
            filename = msg["filename"]
            self.add_bubble(sender, f"Shared file: {filename}")

            if self.root.state() == "iconic":
                self.notify("File received", f"{sender} shared {filename}")

        elif mtype == "rooms":
            messagebox.showinfo("Rooms", "\n".join(msg.get("rooms")))

    # -----------------------------------------------------------
    # Close
    # -----------------------------------------------------------
    def on_close(self):
        try:
            if self.sock:
                self.sock.close()
        except:
            pass
        self.root.destroy()


# ============================================================
#                     MAIN ENTRY
# ============================================================

if __name__ == "__main__":
    root = tk.Tk()
    app = ChatClientGUI(root)
    root.mainloop()

