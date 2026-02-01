import customtkinter as ctk
import subprocess, os, sys, json, threading, queue
import shutil
import winreg
import psutil
import time
import re
from dataclasses import dataclass, asdict
from uuid import uuid4
from typing import Optional
from tkinter import filedialog
from tkinter import messagebox



# ===================== CONFIG =====================
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

APP_NAME = "Esparcraft Server Launcher"
APP_SIZE = "1300x760"
DATA_FILE = "servers.json"
CREATE_NO_WINDOW = 0x08000000
JAVA_EXE = None
JAVA_VERSION_STR = "No detectado"
JAVA_MAJOR = None





def app_dir():
    return os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
        else os.path.dirname(os.path.abspath(__file__))

def data_path(filename):
    base = os.getenv("APPDATA") or app_dir()
    path = os.path.join(base, "EsparcraftLauncher")
    os.makedirs(path, exist_ok=True)
    return os.path.join(path, filename)

def parse_java_major(version_line: str) -> Optional[int]:
    try:
        # java version "17.0.10"
        # openjdk version "21.0.2"
        parts = version_line.split('"')
        if len(parts) >= 2:
            major = parts[1].split(".")[0]
            return int(major)
    except:
        pass
    return None


def get_java_version(java_exe: str) -> str:
    try:
        result = subprocess.run(
            [java_exe, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=CREATE_NO_WINDOW
        )

        output = result.stderr.splitlines()
        if output:
            return output[0]  # Ej: 'java version "17.0.10" 2024-01-16'
    except Exception:
        pass

    return "Versión desconocida"

def find_java_exe() -> Optional[str]:
    # 1️⃣ JAVA_HOME
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        java = os.path.join(java_home, "bin", "java.exe")
        if os.path.exists(java):
            return java

    # 2️⃣ Registro de Windows (Oracle / OpenJDK / Adoptium)
    reg_paths = [
        r"SOFTWARE\JavaSoft\Java Runtime Environment",
        r"SOFTWARE\JavaSoft\JDK",
        r"SOFTWARE\Eclipse Adoptium\JDK",
        r"SOFTWARE\Eclipse Adoptium\JRE",
    ]

    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for reg_path in reg_paths:
            try:
                with winreg.OpenKey(root, reg_path) as key:
                    current, _ = winreg.QueryValueEx(key, "CurrentVersion")
                    with winreg.OpenKey(key, current) as sub:
                        java_home, _ = winreg.QueryValueEx(sub, "JavaHome")
                        java = os.path.join(java_home, "bin", "java.exe")
                        if os.path.exists(java):
                            return java
            except OSError:
                pass

    # 3️⃣ PATH
    java = shutil.which("java")
    if java:
        return java

    return None


def center_window(win, width, height):
    win.update_idletasks()
    x = (win.winfo_screenwidth() // 2) - (width // 2)
    y = (win.winfo_screenheight() // 2) - (height // 2)
    win.geometry(f"{width}x{height}+{x}+{y}")

def get_process_cpu_percent(proc: psutil.Process) -> float:
    """
    Devuelve el uso de CPU normalizado al total del sistema (0–100%)
    """
    return proc.cpu_percent(interval=None) / psutil.cpu_count()



def update_server_performance(server):
    if not server.running or not hasattr(server, "_ps_process"):
        server.cached_cpu = None
        server.cached_ram = None
        return

    now = time.time()
    if now - server.last_perf_update < 1.2:
        return

    try:
        p = server._ps_process
        server.cached_cpu = get_process_cpu_percent(p)
        server.cached_ram = p.memory_info().rss / (1024 * 1024)
        server.last_perf_update = now
    except:
        server.cached_cpu = None
        server.cached_ram = None




# ===================== MODELS =====================
@dataclass
class ServerConfig:
    id: str
    name: str
    jar: str
    ram_min: int
    ram_max: int
    path: str
    auto_restart: bool = False


class ServerRuntime:

    def __init__(self, config: ServerConfig):
        self.config = config
        self.process = None
        self.logs = []
        self.log_queue = queue.Queue()
        self.known_players = set()   # jugadores vistos alguna vez (por joins o usercache)

        self.running = False        # proceso existe
        self.ready = False          # aparece "Done"
        self.stopping = False       # stop enviado
        self.starting = False       # arrancando

        self.last_perf_update = 0
        self.cached_cpu = None
        self.cached_ram = None

            # ---- Players tracking ----
        self.players_online: list[str] = []
        self.players_offline: list[str] = []   # historial simple de vistos (no “todos los del server”)
        self._players_online_set = set()
        self._players_offline_set = set()
        self.last_players_update = 0.0
        self.players_dirty = True  # fuerza render inicial en la vista jugadores
        self.usercache = {}        # name_lower -> uuid string (sin guiones normalmente)

        self.players_changed = set()  # nombres que cambiaron (join/leave)


    @property
    def status(self):
        return "online" if self.running else "offline"


# ===================== APP =====================
class EsparcraftLauncher(ctk.CTk):
    def _players_ui_make_online_card(self, server: ServerRuntime, parent, name: str):
        """Crea una tarjeta compacta (username + uuid + botones) y la devuelve."""
        ops_set = { (o.get("name","") or "").lower() for o in self._read_ops(server) }
        uuid = self._uuid_for_player(server, name)

        name_font = ctk.CTkFont(size=12, weight="bold")
        sub_font = ctk.CTkFont(size=10)

        row = ctk.CTkFrame(parent, corner_radius=12)
        row.grid_columnconfigure(0, weight=1)

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=10, pady=8)

        top = ctk.CTkFrame(left, fg_color="transparent")
        top.pack(anchor="w")

        name_label = ctk.CTkLabel(top, text=name, font=name_font)
        name_label.pack(side="left")

        op_badge = ctk.CTkLabel(
            top, text="OP", text_color="white",
            fg_color="#f59e0b", corner_radius=10, padx=7, pady=1
        )
        if name.lower() in ops_set:
            op_badge.pack(side="left", padx=(6, 0))

        uuid_label = ctk.CTkLabel(left, text=f"UUID: {uuid}", font=sub_font, text_color="#9ca3af")
        uuid_label.pack(anchor="w", pady=(2, 0))

        right = ctk.CTkFrame(row, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e", padx=10, pady=8)

        def _send_cmd(cmd: str):
            if not server.running or not server.process:
                return
            try:
                server.process.stdin.write(cmd + "\n")
                server.process.stdin.flush()
            except Exception:
                pass

        def kick_player():
            if messagebox.askyesno("Kick", f"¿Expulsar a {name}?"):
                _send_cmd(f"kick {name}")

        def ban_player():
            if messagebox.askyesno("Ban", f"¿Banear a {name}?"):
                _send_cmd(f"ban {name}")

        def deop_player():
            if messagebox.askyesno("DeOP", f"¿Quitar OP a {name}?"):
                _send_cmd(f"deop {name}")

        btn_style = dict(width=54, height=24, fg_color="#374151", hover_color="#4b5563")
        ctk.CTkButton(right, text="Kick", command=kick_player, **btn_style).pack(side="left", padx=(0, 8))
        ctk.CTkButton(right, text="Ban", command=ban_player,
                    fg_color="#7f1d1d", hover_color="#450a0a",
                    width=54, height=24).pack(side="left", padx=(0, 8))
        ctk.CTkButton(right, text="DeOP", command=deop_player, **btn_style).pack(side="left")

        # guarda refs para updates
        row._name_label = name_label
        row._uuid_label = uuid_label
        row._op_badge = op_badge
        return row


    def _players_ui_update_online_card(self, server: ServerRuntime, card, name: str):
        """Actualiza uuid y badge OP de una tarjeta existente."""
        ops_set = { (o.get("name","") or "").lower() for o in self._read_ops(server) }
        uuid = self._uuid_for_player(server, name)

        card._name_label.configure(text=name)
        card._uuid_label.configure(text=f"UUID: {uuid}")

        # mostrar/ocultar OP badge
        # (pack_info falla si no está packed; usamos try)
        if name.lower() in ops_set:
            try:
                card._op_badge.pack_info()
            except Exception:
                card._op_badge.pack(side="left", padx=(6, 0))
        else:
            try:
                card._op_badge.pack_forget()
            except Exception:
                pass


    def _players_ui_sync_online(self, server: ServerRuntime):
        """
        Sincroniza ONLINE incremental:
        - agrega tarjetas nuevas
        - elimina tarjetas de jugadores que ya no están
        - reordena según server.players_online (mantiene orden de esa lista)
        - aplica búsqueda
        """
        ui = getattr(self, "_players_ui", None)
        if not ui or ui.get("server_id") != server.config.id:
            return
        if ui["tab_var"].get() != "ONLINE":
            return

        parent = ui["list_frame"]
        cards = ui["cards_online"]
        q = ui["search_var"].get().strip().lower()

        # refrescar usercache (opcional: solo cuando hay cambios)
        self._load_usercache(server)
        # sanitizar lista (por si quedaron duplicados)
        seen = set()
        clean = []
        for n in server.players_online:
            if n not in seen:
                seen.add(n)
                clean.append(n)
        server.players_online = clean
        server._players_online_set = set(clean)
        desired = list(server.players_online)

        # eliminar tarjetas de los que ya no están online
        for name in list(cards.keys()):
            if name not in server._players_online_set:
                cards[name].destroy()
                del cards[name]

        # crear/actualizar tarjetas requeridas
        for name in desired:
            if name not in cards:
                cards[name] = self._players_ui_make_online_card(server, parent, name)
            else:
                self._players_ui_update_online_card(server, cards[name], name)

        # reordenar + filtrar por búsqueda
        visible_count = 0
        for i, name in enumerate(desired):
            card = cards.get(name)
            if not card:
                continue

            # filtro búsqueda
            if q and (q not in name.lower()) and (q not in self._uuid_for_player(server, name).lower()):
                card.grid_forget()
                continue

            card.grid(row=visible_count, column=0, sticky="ew", padx=10, pady=6)
            visible_count += 1

        ui["counter_label"].configure(text=f"{visible_count} online")

    def _tick_background(self):
        """
        Procesa colas de logs de TODOS los servidores:
        - añade líneas a server.logs
        - parsea join/leave
        - marca players_dirty cuando corresponda
        """
        for server in self.servers.values():
            try:
                while True:
                    line = server.log_queue.get_nowait()
                    server.logs.append(line)

                    # parseo de players (en vivo)
                    self._try_parse_join_leave_from_log_line(server, line)

                    # (opcional) limitar tamaño de logs para no consumir RAM
                    if len(server.logs) > 5000:
                        server.logs = server.logs[-3000:]
            except queue.Empty:
                pass

        self.after(80, self._tick_background)  # 12.5 veces/seg, ligero
    # ---------- LOG TAGS ----------
    def _configure_console_tags(self, console):
        console.tag_config("INFO", foreground="#cfcfcf")
        console.tag_config("WARN", foreground="#f1c40f")
        console.tag_config("ERROR", foreground="#e74c3c")
        console.tag_config("SUCCESS", foreground="#2ecc71")
        console.tag_config("COMMAND", foreground="#3498db")
        console.tag_config("SYSTEM", foreground="#9b59b6")

    def _get_log_tag(self, line: str) -> str:
        u = line.upper()
        if u.startswith(">"):
            return "COMMAND"
        if "ERROR" in u or "SEVERE" in u or "FATAL" in u:
            return "ERROR"
        if "WARN" in u or "WARNING" in u:
            return "WARN"
        if any(x in u for x in ["DONE", "STARTED", "LISTENING"]):
            return "SUCCESS"
        if any(x in u for x in ["LOADING", "SAVING", "STOPPING"]):
            return "SYSTEM"
        return "INFO"

    def _insert_colored_log(self, console, line):
        tag = self._get_log_tag(line)
        if not self.log_filters.get(tag, True):
            return
        console.insert("end", line + "\n", tag)

    # --- patrones globales (a nivel de clase) ---
    _JOIN_PATTERNS = [
        # Vanilla / Spigot / Paper: "PlayerName joined the game"
        re.compile(r"(?i)\b([A-Za-z0-9_]{3,16}) joined the game\b"),
    ]

    _LEAVE_PATTERNS = [
        # Vanilla / Spigot / Paper: "PlayerName left the game"
        re.compile(r"(?i)\b([A-Za-z0-9_]{3,16}) left the game\b"),
        # Paper: "PlayerName lost connection: ..."
        re.compile(r"(?i)\b([A-Za-z0-9_]{3,16}) lost connection\b"),
        # Otros: "PlayerName has disconnected"
        re.compile(r"(?i)\b([A-Za-z0-9_]{3,16}) has disconnected\b"),
    ]

    def _request_players_list(self, server: "ServerRuntime"):
        # Desactivado: ya no usamos "list", solo logs join/leave
        return

    def _player_set_online(self, server: "ServerRuntime", name: str):
        name = self._normalize_player_name(name)
        
        if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", name):
            return
        server.known_players.add(name)
        if not name:
            return
        server.players_changed.add(name)
        if name not in server._players_online_set:
            server._players_online_set.add(name)
            server.players_online.append(name)

        if name in server._players_offline_set:
            server._players_offline_set.discard(name)
            server.players_offline = [n for n in server.players_offline if n != name]

        server.last_players_update = time.time()
        server.players_dirty = True
    
    def _normalize_player_name(self, name: str) -> str:
        name = (name or "").strip()

        # Si viene con prefijo numérico (ej: 93mLoconothor) -> Loconothor
        m = re.match(r"^\d+[A-Za-z]([A-Za-z0-9_]{2,15})$", name)
        if m:
            candidate = m.group(1)
            # candidate ya empieza con letra, longitud 3-16 total garantizada por el regex
            return candidate

        return name

    def _player_set_offline(self, server: "ServerRuntime", name: str):
        name = self._normalize_player_name(name)
        
        if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", name):
            return
        server.known_players.add(name)
        if not name:
            return
        server.players_changed.add(name)
        if name in server._players_online_set:
            server._players_online_set.discard(name)
            server.players_online = [n for n in server.players_online if n != name]

        if name not in server._players_offline_set:
            server._players_offline_set.add(name)
            server.players_offline.append(name)

        server.last_players_update = time.time()
        server.players_dirty = True

    def _try_parse_join_leave_from_log_line(self, server: "ServerRuntime", line: str) -> bool:
        s = line.strip()

        for rx in self._JOIN_PATTERNS:
            m = rx.search(s)
            if m:
                self._player_set_online(server, m.group(1))
                return True

        for rx in self._LEAVE_PATTERNS:
            m = rx.search(s)
            if m:
                self._player_set_offline(server, m.group(1))
                return True

        return False

    def _try_parse_players_from_log_line(self, server: "ServerRuntime", line: str) -> bool:
        # Desactivado: ya no usamos salida de "list"
        return False

    def open_players_manager(self, server: ServerRuntime):
        self.current_players = server.config.id
        self.show_players_manager()

    def _load_usercache(self, server: ServerRuntime):
        """
        Carga usercache.json para mapear name->uuid.
        Se puede llamar al abrir la vista o al refrescar.
        """
        path = os.path.join(server.config.path, "usercache.json")
        if not os.path.exists(path):
            server.usercache = {}
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cache = {}
            if isinstance(data, list):
                for it in data:
                    name = (it.get("name") or "").strip()
                    uuid = (it.get("uuid") or "").strip()
                    if name and uuid:
                        cache[name.lower()] = uuid
                        server.known_players.add(name)
            server.usercache = cache
        
        except Exception:
            server.usercache = {}

    def _get_offline_players(self, server: ServerRuntime) -> list[str]:
        # offline = conocidos - online (comparación case-insensitive)
        online_lower = {n.lower() for n in server._players_online_set}

        offline = []
        seen_lower = set()

        for n in server.known_players:
            nl = n.lower()
            if nl in online_lower:
                continue
            if nl in seen_lower:
                continue
            seen_lower.add(nl)
            offline.append(n)

        offline.sort(key=str.lower)
        return offline
    def _uuid_for_player(self, server: ServerRuntime, name: str) -> str:
        return server.usercache.get(name.lower(), "—")

    def _read_ops(self, server: ServerRuntime) -> list[dict]:
        """Lee ops.json. Devuelve lista de dicts: {name, uuid, level, bypassesPlayerLimit}"""
        ops_file = os.path.join(server.config.path, "ops.json")
        if not os.path.exists(ops_file):
            return []

        try:
            with open(ops_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            # data suele ser lista
            if isinstance(data, list):
                # normalizar claves que a veces faltan
                out = []
                for it in data:
                    out.append({
                        "name": it.get("name", "Unknown"),
                        "uuid": it.get("uuid", ""),
                        "level": it.get("level", ""),
                        "bypassesPlayerLimit": it.get("bypassesPlayerLimit", False),
                    })
                return out
        except Exception:
            return []

        return []


    



    # ---------- INIT ----------
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.update_idletasks()
        self.players_changed = set()   # nombres que cambiaron (join/leave)

        w, h = map(int, APP_SIZE.split("x"))
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()

        x = (screen_w // 2) - (w // 2)
        y = (screen_h // 2) - (h // 2)

        self.geometry(f"{w}x{h}+{x}+{y}")


        self.servers: dict[str, ServerRuntime] = {}
        self.current_players: Optional[str] = None
        self.current_console: Optional[str] = None
        self.current_plugins: Optional[str] = None
        self.console_widget = None

        self.log_filters = {
            "INFO": True,
            "WARN": True,
            "ERROR": True,
            "SUCCESS": True,
            "SYSTEM": True,
            "COMMAND": True
        }
        self._plugins_cache = {}          # plugins_dir -> list[dict]
        self._plugins_search_after_id = None

        global JAVA_EXE, JAVA_VERSION_STR, JAVA_MAJOR

        JAVA_EXE = find_java_exe()
        if JAVA_EXE:
            JAVA_VERSION_STR = get_java_version(JAVA_EXE)
            JAVA_MAJOR = parse_java_major(JAVA_VERSION_STR)
        else:
            JAVA_VERSION_STR = "Java no encontrado"


        self._build_ui()
        self._load_servers()

        # seleccionar primer servidor si existe
        if self.servers:
            self.current_console = next(iter(self.servers))

        self.show_dashboard()
        self.after(100, self._tick_background)
        self._console_last_index = 0


    # ===================== UI =====================
    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=240, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="ns")

        ctk.CTkLabel(
            self.sidebar,
            text="Tirano\nStudios",
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w", padx=20, pady=20)

        ctk.CTkButton(self.sidebar, text="Dashboard", command=self.show_dashboard)\
            .pack(fill="x", padx=15, pady=5)

        ctk.CTkButton(self.sidebar, text="Consola", command=self.show_console)\
            .pack(fill="x", padx=15, pady=5)

        ctk.CTkButton(self.sidebar, text="Plugins", command=self.show_plugins_manager)\
            .pack(fill="x", padx=15, pady=5)
        
        ctk.CTkButton(self.sidebar, text="Jugadores", command=self.show_players_manager)\
            .pack(fill="x", padx=15, pady=5)

        self.content = ctk.CTkFrame(self, corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

    # ===================== DASHBOARD =====================
    def show_dashboard(self):
        self._clear_content()

        container = ctk.CTkScrollableFrame(self.content)
        container.pack(fill="both", expand=True, padx=30, pady=30)

        header = ctk.CTkFrame(container)
        header.pack(fill="x", pady=(0, 20))

        ctk.CTkLabel(header, text="Servidores",
                     font=ctk.CTkFont(size=26, weight="bold")).pack(side="left")

        ctk.CTkButton(
            header,
            text="+ Nuevo Servidor",
            command=lambda: self.open_server_modal()
        ).pack(side="right")



        grid = ctk.CTkFrame(container)
        grid.pack(fill="both", expand=True)

        columns = 3  # puedes cambiar a 2 o 4
        for i in range(columns):
            grid.grid_columnconfigure(i, weight=1, uniform="cards")

        row = col = 0

        for server in self.servers.values():
            self._server_card(grid, server, row, col)
            col += 1
            if col >= columns:
                col = 0
                row += 1


    def _server_card(self, parent, server: ServerRuntime, row: int, col: int):
        card = ctk.CTkFrame(
            parent,
            width=300,
            height=360,
            corner_radius=20
        )
        card.grid(row=row, column=col, padx=22, pady=22, sticky="n")
        card.grid_propagate(False)

        # ================= HEADER =================
        header = ctk.CTkFrame(card, height=70, corner_radius=20)
        header.pack(fill="x", padx=8, pady=8)
        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text=server.config.name,
            font=ctk.CTkFont(size=18, weight="bold"),
            wraplength=240,
            justify="center"
        ).pack(expand=True)



        # ================= STATUS =================



        status_color = "#22c55e" if server.status == "online" else "#ef4444"
        status_text = "ONLINE" if server.status == "online" else "OFFLINE"

        status_frame = ctk.CTkFrame(card, fg_color="transparent")
        status_frame.pack(anchor="w",padx=20, pady=(0, 5))



        perf_frame = ctk.CTkFrame(card, fg_color="transparent")
        perf_frame.pack(anchor="w",padx=20, pady=(0, 10))

        cpu_label = ctk.CTkLabel(perf_frame, text="CPU: -- %")
        cpu_label.pack(side="left", padx=(0, 15))

        ram_label = ctk.CTkLabel(perf_frame, text="RAM: -- MB")
        ram_label.pack(side="left")

        def update_performance():
            update_server_performance(server)

            if server.cached_cpu is not None:
                cpu_label.configure(text=f"CPU: {server.cached_cpu:.1f} %")
                ram_label.configure(text=f"RAM: {server.cached_ram:.0f} MB")
            else:
                cpu_label.configure(text="CPU: -- %")
                ram_label.configure(text="RAM: -- MB")





        def get_status():
            if not server.running:
                return "OFFLINE", "#ef4444"
            if server.stopping:
                return "STOPPING", "#f97316"
            if server.starting:
                return "IN PROGRESS", "#f59e0b"
            if server.ready:
                return "ONLINE", "#22c55e"
            return "IN PROGRESS", "#f59e0b"

        text, color = get_status()

        ctk.CTkLabel(
            status_frame,
            text="●",
            text_color=color,
            font=ctk.CTkFont(size=14)
        ).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(
            status_frame,
            text=text,
            text_color=color
        ).pack(side="left")


        # ================= JAVA INFO =================
        if JAVA_MAJOR and JAVA_MAJOR >= 17:
            java_text = f"Java {JAVA_MAJOR} (OK)"
            java_color = "#22c55e"
        elif JAVA_MAJOR:
            java_text = f"Java {JAVA_MAJOR} (Incompatible)"
            java_color = "#ef4444"
        else:
            java_text = "Java no detectado"
            java_color = "#f59e0b"

        java_frame = ctk.CTkFrame(card, fg_color="transparent")
        java_frame.pack(pady=(6, 12))

        ctk.CTkLabel(
            java_frame,
            text="☕",
            font=ctk.CTkFont(size=16)
        ).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(
            java_frame,
            text=java_text,
            text_color=java_color
        ).pack(side="left")

        # ================= DIVIDER =================
        divider = ctk.CTkFrame(card, height=1, fg_color="#2a2a2a")
        divider.pack(fill="x", padx=20, pady=8)

        # ================= ACTIONS =================
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(expand=True, fill="x", padx=24)

        ctk.CTkButton(
            actions,
            text="👥 Jugadores",
            fg_color="#374151",
            command=lambda s=server: self.open_players_manager(s)
        ).pack(fill="x", pady=4)

        main_btn_color = "#2563eb"

        if server.status == "offline":
            ctk.CTkButton(
                actions,
                text="▶ Iniciar servidor",
                fg_color=main_btn_color,
                command=lambda s=server: self.start_server(s)
            ).pack(fill="x", pady=6)
        else:
            ctk.CTkButton(
                actions,
                text="⏹ Detener servidor",
                fg_color="#dc2626",
                hover_color="#b91c1c",
                command=lambda s=server: self.stop_server(s)
            ).pack(fill="x", pady=6)

        ctk.CTkButton(
            actions,
            text="🖥 Abrir consola",
            fg_color="#374151",
            command=lambda s=server: self.open_console(s)
        ).pack(fill="x", pady=4)

        ctk.CTkButton(
            actions,
            text="🧩 Plugins",
            fg_color="#374151",
            command=lambda s=server: self.open_plugins_manager(s)
        ).pack(fill="x", pady=4)

        ctk.CTkButton(
            actions,
            text="⚙ Configuración",
            fg_color="#374151",
            command=lambda s=server: self.open_server_modal(s)
        ).pack(fill="x", pady=(4, 10))
        
        def loop_perf():
            update_performance()
            card.after(1000, loop_perf)

        loop_perf()


    # ===================== SERVER =====================
    def start_server(self, server: ServerRuntime):
        if server.running:
            return

        cfg = server.config
        jar_path = os.path.join(cfg.path, cfg.jar)
        if not os.path.exists(jar_path):
            return

        java = JAVA_EXE
        if not java:
            msg = "ERROR: Java no encontrado. Instala Java 17+ y vuelve a intentar."
            server.log_queue.put(msg)  # ← Solo queue, NO logs.append
            return

        cmd = [
            java,
            f"-Xms{cfg.ram_min}G",
            f"-Xmx{cfg.ram_max}G",
            "-jar", jar_path, "nogui"
        ]

        def run():
            server.running = True
            server.starting = True
            server.ready = False
            server.stopping = False

            server.process = subprocess.Popen(
                cmd,
                cwd=cfg.path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
                text=True,
                bufsize=1,
                encoding='utf-8',      # ← AGREGAR ESTO
                errors='replace'       # ← AGREGAR ESTO (reemplaza chars inválidos)
            )
            p = psutil.Process(server.process.pid)
            p.cpu_percent(interval=None)
            server._ps_process = p

            for line in server.process.stdout:
                line = line.rstrip()
                server.log_queue.put(line)

                if "Done" in line:
                    server.ready = True
                    server.starting = False
            
            ret = server.process.wait()

            server.running = False
            server.ready = False
            server.starting = False
            server.stopping = False

            for n in list(server._players_online_set):
                self._player_set_offline(server, n)
            server._players_online_set.clear()
            server.players_online.clear()

            server.log_queue.put(f"SYSTEM: Proceso finalizado (code={ret})")

            self.after(0, self.show_dashboard)

        msg = "SYSTEM: Iniciando servidor..."
        server.log_queue.put(msg)  # ← Solo queue, QUITAR server.logs.append(msg)

        threading.Thread(target=run, daemon=True).start()
        self.open_console(server)

    def _is_scrolled_to_bottom(self, textbox):
        return textbox.yview()[1] >= 0.99


    def stop_server(self, server: ServerRuntime):
        if server.process and server.running:
            try:
                server.process.stdin.write("stop\n")
                server.process.stdin.flush()
            except:
                pass

    def stop_server_clean(self, server: ServerRuntime):
        if server.process and server.running and not server.stopping:
            try:
                server.stopping = True
                server.starting = False
                server.logs.append("SYSTEM: Deteniéndose...")
                server.process.stdin.write("stop\n")
                server.process.stdin.flush()
            except:
                pass



    def kill_server(self, server: ServerRuntime):
        if server.process:
            try:
                server.process.kill()
                server.running = False
                server.logs.append("ERROR: Servidor finalizado forzosamente")
                self.after(0, self.show_dashboard)
            except:
                pass


    def _rerender_console(self):
        if not self.console_widget:
            return
        server = self.servers.get(self.current_console)
        if not server:
            return

        self.console_widget.configure(state="normal")
        self.console_widget.delete("1.0", "end")
        for line in server.logs:
            self._insert_colored_log(self.console_widget, line)
        self.console_widget.configure(state="disabled")
        self.console_widget.see("end")

    def show_console(self):
        self._clear_content()

        if not self.servers:
            ctk.CTkLabel(self.content, text="No hay servidores creados").pack(pady=40)
            return

        if not self.current_console or self.current_console not in self.servers:
            self.current_console = next(iter(self.servers))


        server = self.servers[self.current_console]

        frame = ctk.CTkFrame(self.content)
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        # ---------- SELECTOR DE SERVIDOR ----------
        server_ids = list(self.servers.keys())
        server_names = {
            sid: self.servers[sid].config.name for sid in server_ids
        }

        selected_name = ctk.StringVar(
            value=server_names[self.current_console]
        )

        def on_select(name):
            for sid, sname in server_names.items():
                if sname == name:
                    self.current_console = sid
                    self._rerender_console()
                    break

        selector = ctk.CTkOptionMenu(
            frame,
            values=list(server_names.values()),
            variable=selected_name,
            command=on_select
        )
        selector.pack(anchor="w", pady=(5, 10))

        ctk.CTkLabel(
            frame,
            text="Consola",
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w")
        # ---------- STATUS BAR ----------
        status_bar = ctk.CTkFrame(frame)
        status_bar.pack(fill="x", pady=(0, 6))

        # ---------- TOP BAR (Filtro + Acciones) ----------
        top_bar = ctk.CTkFrame(frame)
        top_bar.pack(fill="x", pady=(5, 10))

        status_dot = ctk.CTkLabel(status_bar, text="●", font=ctk.CTkFont(size=14))
        status_dot.pack(side="left", padx=(0, 6))

        status_label = ctk.CTkLabel(status_bar, text="OFFLINE")
        status_label.pack(side="left")

        cpu_label = ctk.CTkLabel(status_bar, text="CPU: -- %")
        cpu_label.pack(side="right", padx=(10, 0))

        ram_label = ctk.CTkLabel(status_bar, text="RAM: -- MB")
        ram_label.pack(side="right", padx=(10, 0))

        self.console_status_dot = status_dot
        self.console_status_label = status_label
        self.console_cpu_label = cpu_label
        self.console_ram_label = ram_label


        # --- IZQUIERDA: FILTRO + LIMPIAR ---
        left = ctk.CTkFrame(top_bar, fg_color="transparent")
        left.pack(side="left")

        ctk.CTkLabel(left, text="Filtro:").pack(side="left", padx=(0, 8))

        filter_var = ctk.StringVar(value="ALL")

        def apply_filter(choice):
            if choice == "ALL":
                for k in self.log_filters:
                    self.log_filters[k] = True
            else:
                for k in self.log_filters:
                    self.log_filters[k] = (k == choice)
            self._rerender_console()

        ctk.CTkOptionMenu(
            left,
            values=["ALL"] + list(self.log_filters.keys()),
            variable=filter_var,
            command=apply_filter
        ).pack(side="left")

        def clear_console():
            server.logs.clear()
            self.console_widget.configure(state="normal")
            self.console_widget.delete("1.0", "end")
            self.console_widget.configure(state="disabled")

        ctk.CTkButton(
            left,
            text="🧹 Limpiar",
            width=90,
            fg_color="#374151",
            command=clear_console
        ).pack(side="left", padx=(10, 0))

        # --- DERECHA: BOTONES ---
        right = ctk.CTkFrame(top_bar, fg_color="transparent")
        right.pack(side="right")

        state = "normal" if server.running else "disabled"

        ctk.CTkButton(
            right,
            text="⏹ Detener",
            fg_color="#dc2626",
            hover_color="#b91c1c",
            state=state,
            command=lambda: self.stop_server_clean(server)
        ).pack(side="left", padx=(0, 10))

        def confirm_kill():
            if not server.running:
                return
            if messagebox.askyesno(
                "Confirmar",
                "⚠ ¿Seguro que deseas FINALIZAR el servidor?\nEsto puede causar corrupción de datos."
            ):
                self.kill_server(server)

        ctk.CTkButton(
            right,
            text="☠ Kill",
            fg_color="#7f1d1d",
            hover_color="#450a0a",
            state=state,
            command=confirm_kill
        ).pack(side="left")


        console = ctk.CTkTextbox(frame)
        console.pack(fill="both", expand=True, pady=10)

        self._configure_console_tags(console)
        self.console_widget = console

        self._rerender_console()
        self._console_last_index = len(server.logs)

        entry = ctk.CTkEntry(frame)
        entry.pack(fill="x")

        def send(event=None):
            cmd = entry.get().strip()
            if cmd and server.process:
                server.process.stdin.write(cmd + "\n")
                server.process.stdin.flush()
                entry.delete(0, "end")

        entry.bind("<Return>", send)
        self.after(100, self._update_console)

    def open_console(self, server: ServerRuntime):
        self.current_console = server.config.id
        self.current_plugins = None
        self.show_console()

    @staticmethod
    def cpu_color(cpu):
        if cpu < 40:
            return "#22c55e"
        if cpu < 70:
            return "#f59e0b"
        return "#ef4444"

    def _update_console(self):
        if not self.current_console or not self.console_widget:
            return

        server = self.servers.get(self.current_console)
        if not server:
            return

        # Añadir solo lo nuevo desde server.logs (sin tocar log_queue)
        start = getattr(self, "_console_last_index", 0)
        new_lines = server.logs[start:]
        if new_lines:
            auto_scroll = self._is_scrolled_to_bottom(self.console_widget)

            self.console_widget.configure(state="normal")
            for line in new_lines:
                self._insert_colored_log(self.console_widget, line)
            self.console_widget.configure(state="disabled")

            if auto_scroll:
                self.console_widget.see("end")

            self._console_last_index = len(server.logs)

        

        



        # ---------- ACTUALIZAR ESTADO ----------
        if hasattr(self, "console_status_dot"):
            if not server.running:
                color = "#ef4444"
                text = "OFFLINE"
            elif server.stopping:
                color = "#f97316"
                text = "STOPPING"
            elif server.starting:
                color = "#f59e0b"
                text = "IN PROGRESS"
            elif server.ready:
                color = "#22c55e"
                text = "ONLINE"
            else:
                color = "#f59e0b"
                text = "IN PROGRESS"

            self.console_status_dot.configure(text_color=color)
            self.console_status_label.configure(text=text)


        # ---------- ACTUALIZAR PERFORMANCE ----------
        update_server_performance(server)

        if server.cached_cpu is not None:
            color = self.cpu_color(server.cached_cpu)
            self.console_cpu_label.configure(
                text=f"CPU: {server.cached_cpu:.1f} %",
                text_color=color
            )
            self.console_ram_label.configure(text=f"RAM: {server.cached_ram:.0f} MB")
        else:
            self.console_cpu_label.configure(text="CPU: -- %", text_color="#cfcfcf")
            self.console_ram_label.configure(text="RAM: -- MB")


        self.after(100, self._update_console)


    def _players_render_current(self, root, server: ServerRuntime, query: str, mode: str, list_frame, counter_label):
        # limpiar contenedor
        for w in list_frame.winfo_children():
            w.destroy()

        q = (query or "").strip().lower()

        if mode == "OPS":
            ops = self._read_ops(server)
            if q:
                ops = [o for o in ops if q in (o.get("name", "").lower())]

            counter_label.configure(text=f"{len(ops)} ops")

            if not ops:
                empty = ctk.CTkFrame(list_frame, fg_color="transparent")
                empty.pack(fill="both", expand=True, pady=30)
                ctk.CTkLabel(empty, text="No hay OPs detectados.", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(0, 6))
                ctk.CTkLabel(empty, text="Se lee desde ops.json del servidor.", text_color="#9ca3af").pack()
                return

            for o in ops:
                card = ctk.CTkFrame(list_frame, corner_radius=14)
                card.pack(fill="x", padx=10, pady=8)
                card.grid_columnconfigure(0, weight=1)

                left = ctk.CTkFrame(card, fg_color="transparent")
                left.grid(row=0, column=0, sticky="w", padx=14, pady=12)

                name = o.get("name", "Unknown")
                uuid = o.get("uuid", "")
                level = o.get("level", "")
                bypass = o.get("bypassesPlayerLimit", False)

                ctk.CTkLabel(left, text=name, font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w")
                ctk.CTkLabel(left, text=f"UUID: {uuid}" if uuid else "UUID: —", text_color="#9ca3af").pack(anchor="w", pady=(2, 0))

                right = ctk.CTkFrame(card, fg_color="transparent")
                right.grid(row=0, column=1, sticky="e", padx=14, pady=12)

                if level != "":
                    ctk.CTkLabel(
                        right, text=f"LEVEL {level}", text_color="white",
                        fg_color="#2563eb", corner_radius=10, padx=10, pady=4
                    ).pack(side="left", padx=(0, 10))

                if bypass:
                    ctk.CTkLabel(
                        right, text="BYPASS", text_color="white",
                        fg_color="#7c3aed", corner_radius=10, padx=10, pady=4
                    ).pack(side="left")
            return

        if mode == "OFFLINE":
            players = list(server.players_offline)
            if q:
                players = [p for p in players if q in p.lower()]

            counter_label.configure(text=f"{len(players)} offline (vistos)")

            if not players:
                empty = ctk.CTkFrame(list_frame, fg_color="transparent")
                empty.pack(fill="both", expand=True, pady=30)
                ctk.CTkLabel(empty, text="No hay jugadores offline aún.", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(0, 6))
                ctk.CTkLabel(empty, text="Se llenará cuando alguien entre y luego salga.", text_color="#9ca3af").pack()
                return

            for name in players:
                card = ctk.CTkFrame(list_frame, corner_radius=14)
                card.pack(fill="x", padx=10, pady=8)
                card.grid_columnconfigure(0, weight=1)

                left = ctk.CTkFrame(card, fg_color="transparent")
                left.grid(row=0, column=0, sticky="w", padx=14, pady=12)

                ctk.CTkLabel(left, text=name, font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w")
                ctk.CTkLabel(left, text="Jugador offline", text_color="#9ca3af").pack(anchor="w", pady=(2, 0))

                right = ctk.CTkFrame(card, fg_color="transparent")
                right.grid(row=0, column=1, sticky="e", padx=14, pady=12)

                ctk.CTkLabel(
                    right, text="OFFLINE", text_color="white",
                    fg_color="#6b7280", corner_radius=10, padx=12, pady=4
                ).pack(side="left")
            return

        # Si llega aquí (ONLINE), no hacemos nada: lo maneja incremental
        counter_label.configure(text="")


    def show_players_manager(self):
        self._clear_content()

        if not self.servers:
            ctk.CTkLabel(self.content, text="No hay servidores creados").pack(pady=40)
            return

        if not self.current_players or self.current_players not in self.servers:
            self.current_players = next(iter(self.servers))

        server = self.servers[self.current_players]

        root = ctk.CTkFrame(self.content, corner_radius=0)
        root.pack(fill="both", expand=True, padx=22, pady=22)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(2, weight=1)

        # ---------- HEADER ----------
        header = ctk.CTkFrame(root, corner_radius=16)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header,
            text="Jugadores",
            font=ctk.CTkFont(size=24, weight="bold")
        ).grid(row=0, column=0, padx=16, pady=(14, 4), sticky="w")

        # selector servidor
        server_ids = list(self.servers.keys())
        server_names = {sid: self.servers[sid].config.name for sid in server_ids}
        selected = ctk.StringVar(value=server_names[self.current_players])

        def on_select(name):
            for sid, sname in server_names.items():
                if sname == name:
                    self.current_players = sid
                    self.show_players_manager()
                    break

        selector = ctk.CTkOptionMenu(
            header,
            values=list(server_names.values()),
            variable=selected,
            command=on_select
        )
        selector.grid(row=0, column=1, padx=16, pady=(14, 4), sticky="e")

        subtitle = ctk.CTkLabel(
            header,
            text=f"Servidor: {server.config.name}  •  Modo: Live (logs)",
            text_color="#9ca3af"
        )
        subtitle.grid(row=1, column=0, padx=16, pady=(0, 14), sticky="w")

        # acciones
        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=1, column=1, padx=16, pady=(0, 14), sticky="e")

        # ---------- TOOLBAR ----------
        toolbar = ctk.CTkFrame(root, corner_radius=16)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        toolbar.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(toolbar, text="Buscar:", text_color="#cbd5e1")\
            .grid(row=0, column=0, padx=(16, 8), pady=12, sticky="w")

        search_var = ctk.StringVar(value="")
        search = ctk.CTkEntry(toolbar, textvariable=search_var, placeholder_text="Steve, Admin...")
        search.grid(row=0, column=1, padx=(0, 12), pady=12, sticky="ew")

        tab_var = ctk.StringVar(value="ONLINE")
        tab = ctk.CTkSegmentedButton(toolbar, values=["ONLINE", "OFFLINE", "OPS"], variable=tab_var)
        tab.grid(row=0, column=2, padx=(0, 12), pady=12, sticky="e")

        counter_label = ctk.CTkLabel(toolbar, text="", text_color="#9ca3af")
        counter_label.grid(row=0, column=3, padx=(0, 16), pady=12, sticky="e")

        # botón refrescar vista (no list)
        def refresh_view():
            self._players_render_current(root, server, search_var.get(), tab_var.get(), list_frame, counter_label)

        ctk.CTkButton(actions, text="⟳ Refrescar vista", fg_color="#374151", command=refresh_view)\
            .pack(side="left")

        # ---------- LISTA ----------
        list_frame = ctk.CTkScrollableFrame(root, corner_radius=16)
        list_frame.grid(row=2, column=0, sticky="nsew")

        # ---------- FOOTER ----------
        footer = ctk.CTkFrame(root, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ctk.CTkLabel(
            footer,
            text="Se actualiza leyendo logs de join/leave. Si abres el launcher con gente ya conectada, no aparecerán hasta que ocurra un evento.",
            text_color="#9ca3af"
        ).pack(anchor="w", padx=8)

        self._players_ui = {
            "server_id": server.config.id,
            "list_frame": list_frame,
            "counter_label": counter_label,
            "search_var": search_var,
            "tab_var": tab_var,
            "cards_offline": {},
            "cards_ops": {},
            "cards_online": {},   # name -> frame
        }
        
        
        self._load_usercache(server)
        self._players_ui_sync_online(server)
        server.players_dirty = False
        server.players_changed.clear()

        # debounce buscador
        if not hasattr(self, "_players_search_after_id"):
            self._players_search_after_id = None

        def schedule_render(*_):
            mode = tab_var.get()

            # limpiar contenedor al cambiar de modo (para evitar mezclar)
            for w in list_frame.winfo_children():
                w.destroy()

            if mode == "ONLINE":
                self._players_ui["cards_online"].clear()
                self._players_ui_sync_online(server)
            elif mode == "OFFLINE":
                self._players_ui["cards_offline"].clear()
                self._players_ui_sync_offline(server)
            else:  # OPS
                self._players_ui_sync_ops(server)

        search_var.trace_add("write", schedule_render)
        tab.configure(command=lambda *_: schedule_render())

        # auto-refresh (solo re-render)
        def loop():
            if not self.content.winfo_exists():
                return
            if self.current_players != server.config.id:
                return

            if server.players_dirty:
                server.players_dirty = False
                server.players_changed.clear()

                mode = tab_var.get()
                if mode == "ONLINE":
                    self._players_ui_sync_online(server)
                elif mode == "OFFLINE":
                    self._players_ui_sync_offline(server)
                elif mode == "OPS":
                    self._players_ui_sync_ops(server)

            root.after(250, loop)

        loop()

    def _players_ui_make_offline_card(self, server: ServerRuntime, parent, name: str):
        uuid = self._uuid_for_player(server, name)

        # detectar OP
        ops_set = { (o.get("name","") or "").lower() for o in self._read_ops(server) }
        is_op = name.lower() in ops_set

        name_font = ctk.CTkFont(size=12, weight="bold")
        sub_font = ctk.CTkFont(size=10)

        row = ctk.CTkFrame(parent, corner_radius=12)
        row.grid_columnconfigure(0, weight=1)

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=10, pady=8)

        # ---- nombre + OP badge ----
        top = ctk.CTkFrame(left, fg_color="transparent")
        top.pack(anchor="w")

        name_label = ctk.CTkLabel(top, text=name, font=name_font)
        name_label.pack(side="left")

        if is_op:
            ctk.CTkLabel(
                top, text="OP", text_color="white",
                fg_color="#f59e0b", corner_radius=10, padx=6, pady=1
            ).pack(side="left", padx=(6, 0))

        # ---- uuid ----
        ctk.CTkLabel(
            left, text=f"UUID: {uuid}", font=sub_font, text_color="#9ca3af"
        ).pack(anchor="w", pady=(2, 0))

        right = ctk.CTkFrame(row, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e", padx=10, pady=8)

        # ---- OFFLINE badge ----
        ctk.CTkLabel(
            right, text="OFFLINE", text_color="white",
            fg_color="#6b7280", corner_radius=10, padx=10, pady=3
        ).pack(side="left")

        return row



    def _players_ui_sync_offline(self, server: ServerRuntime):
        ui = getattr(self, "_players_ui", None)
        if not ui or ui.get("server_id") != server.config.id:
            return
        if ui["tab_var"].get() != "OFFLINE":
            return

        parent = ui["list_frame"]
        cards = ui["cards_offline"]
        q = ui["search_var"].get().strip().lower()

        self._load_usercache(server)
        desired = self._get_offline_players(server)

        # eliminar sobrantes
        for name in list(cards.keys()):
            if name not in desired:
                cards[name].destroy()
                del cards[name]

        # crear faltantes
        for name in desired:
            if name not in cards:
                cards[name] = self._players_ui_make_offline_card(server, parent, name)

        # ordenar / filtrar
        visible = 0
        for name in desired:
            card = cards.get(name)
            if not card:
                continue
            if q and (q not in name.lower()) and (q not in self._uuid_for_player(server, name).lower()):
                card.grid_forget()
                continue
            card.grid(row=visible, column=0, sticky="ew", padx=10, pady=6)
            visible += 1

        ui["counter_label"].configure(text=f"{visible} offline")
    def _players_ui_make_ops_card(self, parent, op: dict):
        name = op.get("name", "Unknown")
        uuid = op.get("uuid", "")
        level = op.get("level", "")
        bypass = op.get("bypassesPlayerLimit", False)

        name_font = ctk.CTkFont(size=12, weight="bold")
        sub_font = ctk.CTkFont(size=10)

        row = ctk.CTkFrame(parent, corner_radius=12)
        row.grid_columnconfigure(0, weight=1)

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=10, pady=8)

        ctk.CTkLabel(left, text=name, font=name_font).pack(anchor="w")
        ctk.CTkLabel(left, text=f"UUID: {uuid or '—'}", font=sub_font, text_color="#9ca3af").pack(anchor="w", pady=(2, 0))

        right = ctk.CTkFrame(row, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e", padx=10, pady=8)

        if level != "":
            ctk.CTkLabel(
                right, text=f"LEVEL {level}", text_color="white",
                fg_color="#2563eb", corner_radius=10, padx=10, pady=3
            ).pack(side="left", padx=(0, 8))

        if bypass:
            ctk.CTkLabel(
                right, text="BYPASS", text_color="white",
                fg_color="#7c3aed", corner_radius=10, padx=10, pady=3
            ).pack(side="left")

        return row


    def _players_ui_sync_ops(self, server: ServerRuntime):
        ui = getattr(self, "_players_ui", None)
        if not ui or ui.get("server_id") != server.config.id:
            return
        if ui["tab_var"].get() != "OPS":
            return

        parent = ui["list_frame"]
        q = ui["search_var"].get().strip().lower()

        ops = self._read_ops(server)
        if q:
            ops = [o for o in ops if q in (o.get("name", "").lower())]

        # reconstrucción simple (ops suelen ser pocos)
        for w in parent.winfo_children():
            w.destroy()

        visible = 0
        for op in ops:
            card = self._players_ui_make_ops_card(parent, op)
            card.grid(row=visible, column=0, sticky="ew", padx=10, pady=6)
            visible += 1

        ui["counter_label"].configure(text=f"{visible} ops")
    # ===================== PLUGINS =====================
    def open_plugins_manager(self, server: ServerRuntime):
        self.current_plugins = server.config.id
        self.show_plugins_manager()

    def _plugins_list(self, plugins_dir: str, force_refresh: bool = False):
        """Lista de plugins con cache para no tocar disco en cada render."""
        if (not force_refresh) and plugins_dir in self._plugins_cache:
            return self._plugins_cache[plugins_dir]

        items = []
        try:
            for file in os.listdir(plugins_dir):
                if not (file.endswith(".jar") or file.endswith(".jar.disabled")):
                    continue
                enabled = file.endswith(".jar")
                name = file.replace(".jar", "").replace(".disabled", "")
                items.append({
                    "file": file,
                    "name": name,
                    "enabled": enabled,
                    "path": os.path.join(plugins_dir, file)
                })
        except FileNotFoundError:
            pass

        items.sort(key=lambda x: x["name"].lower())
        self._plugins_cache[plugins_dir] = items
        return items


    def _toggle_plugin_file(self, plugins_dir: str, filename: str, enabled: bool):
        src = os.path.join(plugins_dir, filename)
        if enabled:
            dst = src + ".disabled"
        else:
            dst = src.replace(".disabled", "")

        os.rename(src, dst)

        # actualizar cache (si existe)
        if plugins_dir in self._plugins_cache:
            items = self._plugins_cache[plugins_dir]
            for it in items:
                if it["file"] == filename:
                    it["file"] = os.path.basename(dst)
                    it["path"] = dst
                    it["enabled"] = not enabled
                    break


    def show_plugins_manager(self):
        self._clear_content()
        if not self.current_plugins or self.current_plugins not in self.servers:
            ctk.CTkLabel(self.content, text="Selecciona un servidor").pack(pady=40)
            return

        server = self.servers[self.current_plugins]
        plugins_dir = os.path.join(server.config.path, "plugins")
        os.makedirs(plugins_dir, exist_ok=True)

        # ---------- Layout base ----------
        root = ctk.CTkFrame(self.content, corner_radius=0)
        root.pack(fill="both", expand=True, padx=22, pady=22)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(2, weight=1)

        # ---------- Header (título + subtítulo + acciones) ----------
        header = ctk.CTkFrame(root, corner_radius=16)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.grid_columnconfigure(0, weight=1)

        title_row = ctk.CTkFrame(header, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        title_row.grid_columnconfigure(0, weight=1)

        left_title = ctk.CTkFrame(title_row, fg_color="transparent")
        left_title.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            left_title,
            text="Plugins",
            font=ctk.CTkFont(size=24, weight="bold")
        ).pack(anchor="w")

        ctk.CTkLabel(
            left_title,
            text=f"Servidor: {server.config.name}  •  Carpeta: plugins/",
            text_color="#9ca3af"
        ).pack(anchor="w", pady=(2, 0))

        actions = ctk.CTkFrame(title_row, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="e")

        def open_plugins_folder():
            # Windows: abre el explorador
            try:
                os.startfile(plugins_dir)
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo abrir la carpeta:\n{e}")

        def refresh():
            self._plugins_cache.pop(plugins_dir, None)  # invalida cache
            self.show_plugins_manager() 

        def add_plugin():
            # selecciona .jar y lo copia a plugins/
            file_path = filedialog.askopenfilename(
                title="Seleccionar plugin .jar",
                filetypes=[("Minecraft Plugin", "*.jar")]
            )
            if not file_path:
                return
            try:
                dst = os.path.join(plugins_dir, os.path.basename(file_path))
                shutil.copy2(file_path, dst)
                self._plugins_cache.pop(plugins_dir, None)
                self.show_plugins_manager()
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo copiar el plugin:\n{e}")

        ctk.CTkButton(actions, text="📁 Abrir carpeta", fg_color="#374151", command=open_plugins_folder)\
            .pack(side="left", padx=(0, 10))
        ctk.CTkButton(actions, text="⟳ Refrescar", fg_color="#374151", command=refresh)\
            .pack(side="left", padx=(0, 10))
        ctk.CTkButton(actions, text="+ Añadir", fg_color="#2563eb", command=add_plugin)\
            .pack(side="left")

        # ---------- Toolbar (buscador + filtros + contador) ----------
        toolbar = ctk.CTkFrame(root, corner_radius=16)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        toolbar.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(toolbar, text="Buscar:", text_color="#cbd5e1")\
            .grid(row=0, column=0, padx=(16, 8), pady=12, sticky="w")

        search_var = ctk.StringVar(value="")
        search = ctk.CTkEntry(toolbar, textvariable=search_var, placeholder_text="Essentials, LuckPerms...")
        search.grid(row=0, column=1, padx=(0, 12), pady=12, sticky="ew")

        filter_var = ctk.StringVar(value="ALL")
        filter_menu = ctk.CTkOptionMenu(
            toolbar,
            variable=filter_var,
            values=["ALL", "ENABLED", "DISABLED"]
        )
        filter_menu.grid(row=0, column=2, padx=(0, 12), pady=12, sticky="e")

        counter_label = ctk.CTkLabel(toolbar, text="", text_color="#9ca3af")
        counter_label.grid(row=0, column=3, padx=(0, 16), pady=12, sticky="e")

        # ---------- Lista (scroll) ----------
        list_frame = ctk.CTkScrollableFrame(root, corner_radius=16)
        list_frame.grid(row=2, column=0, sticky="nsew")

        # ---------- Footer aviso ----------
        footer = ctk.CTkFrame(root, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", pady=(12, 0))

        ctk.CTkLabel(
            footer,
            text="⚠ Los cambios requieren reiniciar el servidor para aplicarse.",
            text_color="#f59e0b"
        ).pack(anchor="w", padx=8)

        # ========== Render ==========
        def render():
            # limpiar lista
            for w in list_frame.winfo_children():
                w.destroy()

            items = self._plugins_list(plugins_dir)

            q = search_var.get().strip().lower()
            f = filter_var.get()

            # filtrar
            filtered = []
            for it in items:
                if q and q not in it["name"].lower():
                    continue
                if f == "ENABLED" and not it["enabled"]:
                    continue
                if f == "DISABLED" and it["enabled"]:
                    continue
                filtered.append(it)

            enabled_count = sum(1 for it in items if it["enabled"])
            counter_label.configure(text=f"{enabled_count}/{len(items)} activos")

            if not items:
                empty = ctk.CTkFrame(list_frame, fg_color="transparent")
                empty.pack(fill="both", expand=True, pady=30)
                ctk.CTkLabel(
                    empty,
                    text="No hay plugins instalados todavía.",
                    font=ctk.CTkFont(size=16, weight="bold")
                ).pack(pady=(0, 6))
                ctk.CTkLabel(
                    empty,
                    text="Usa “+ Añadir” para copiar un .jar a la carpeta plugins.",
                    text_color="#9ca3af"
                ).pack()
                return

            if not filtered:
                empty = ctk.CTkFrame(list_frame, fg_color="transparent")
                empty.pack(fill="both", expand=True, pady=30)
                ctk.CTkLabel(
                    empty,
                    text="No hay resultados con ese filtro/búsqueda.",
                    font=ctk.CTkFont(size=16, weight="bold")
                ).pack(pady=(0, 6))
                ctk.CTkLabel(empty, text="Prueba con otro nombre o cambia el filtro.", text_color="#9ca3af").pack()
                return

            # tarjetas de plugin
            for it in filtered:
                card = ctk.CTkFrame(list_frame, corner_radius=14)
                card.pack(fill="x", padx=10, pady=8)

                card.grid_columnconfigure(0, weight=1)

                # Izquierda (nombre + archivo)
                left = ctk.CTkFrame(card, fg_color="transparent")
                left.grid(row=0, column=0, sticky="w", padx=14, pady=12)

                ctk.CTkLabel(
                    left,
                    text=it["name"],
                    font=ctk.CTkFont(size=16, weight="bold")
                ).pack(anchor="w")

                ctk.CTkLabel(
                    left,
                    text=it["file"],
                    text_color="#9ca3af"
                ).pack(anchor="w", pady=(2, 0))

                # Derecha (badge + switch + menu)
                right = ctk.CTkFrame(card, fg_color="transparent")
                right.grid(row=0, column=1, sticky="e", padx=14, pady=12)

                if it["enabled"]:
                    badge_text = "ACTIVO"
                    badge_color = "#16a34a"
                else:
                    badge_text = "DESACTIVADO"
                    badge_color = "#ef4444"

                badge = ctk.CTkLabel(
                    right,
                    text=badge_text,
                    text_color="white",
                    fg_color=badge_color,
                    corner_radius=10,
                    padx=10, pady=4
                )
                badge.pack(side="left", padx=(0, 10))

                sw_var = ctk.BooleanVar(value=it["enabled"])

                def on_toggle(plugin=it, var=sw_var):
                    try:
                        self._toggle_plugin_file(plugins_dir, plugin["file"], plugin["enabled"])
                        render()
                    except Exception as e:
                        messagebox.showerror("Error", f"No se pudo cambiar el estado:\n{e}")
                        var.set(plugin["enabled"])

                switch = ctk.CTkSwitch(
                    right,
                    text="",
                    variable=sw_var,
                    command=on_toggle
                )
                switch.pack(side="left", padx=(0, 10))

                def remove_plugin(plugin=it):
                    if not messagebox.askyesno("Eliminar plugin", f"¿Eliminar '{plugin['name']}'?\n\nSe borrará el archivo del disco."):
                        return
                    try:
                        os.remove(plugin["path"])
                        self.show_plugins_manager()
                    except Exception as e:
                        messagebox.showerror("Error", f"No se pudo eliminar:\n{e}")

                ctk.CTkButton(
                    right,
                    text="🗑",
                    width=42,
                    fg_color="#374151",
                    hover_color="#4b5563",
                    command=remove_plugin
                ).pack(side="left")

        # eventos
        def schedule_render(*_):
            if self._players_search_after_id is not None:
                try:
                    self.after_cancel(self._players_search_after_id)
                except Exception:
                    pass
            self._players_search_after_id = self.after(
                120,
                lambda: (setattr(server, "players_dirty", False),
                        self._players_render_current(root, server, search_var.get(), tab_var.get(), list_frame, counter_label))
            )

            # programa un render corto (debounce)
            self._plugins_search_after_id = self.after(140, render)
            

        def on_search(*_):
            schedule_render()

        search_var.trace_add("write", on_search)                                 
        filter_menu.configure(command=lambda *_: render())

        render()

    # ===================== DATA =====================
    def _save_servers(self):
        path = data_path(DATA_FILE)
        with open(path, "w", encoding="utf-8") as f:
            json.dump([asdict(s.config) for s in self.servers.values()], f, indent=4)

    def _load_servers(self):
        path = data_path(DATA_FILE)
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            for data in json.load(f):
                cfg = ServerConfig(**data)
                self.servers[cfg.id] = ServerRuntime(cfg)

    def _clear_content(self):
        self.console_widget = None
        for w in self.content.winfo_children():
            w.destroy()

            
        # ===================== SERVER MODAL =====================
    def open_server_modal(self, server: Optional[ServerRuntime] = None):
        modal = ctk.CTkToplevel(self)
        modal.title("Configurar Servidor" if server else "Nuevo Servidor")
        modal.geometry("520x600")
        modal.grab_set()

        # centrar modal
        modal.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() // 2) - 260
        y = self.winfo_y() + (self.winfo_height() // 2) - 300
        modal.geometry(f"+{x}+{y}")

        frame = ctk.CTkFrame(modal)
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(
            frame,
            text="Configuración del Servidor",
            font=ctk.CTkFont(size=20, weight="bold")
        ).pack(pady=(0, 20))

        cfg = server.config if server else None

        # ---------- NOMBRE ----------
        ctk.CTkLabel(frame, text="Nombre").pack(anchor="w")
        name = ctk.CTkEntry(frame)
        name.pack(fill="x", pady=5)
        name.insert(0, cfg.name if cfg else "")

        # ---------- RUTA ----------
        ctk.CTkLabel(frame, text="Ruta del servidor").pack(anchor="w")

        path_var = ctk.StringVar(value=cfg.path if cfg else "")

        path_row = ctk.CTkFrame(frame)
        path_row.pack(fill="x", pady=(5, 15))

        path_entry = ctk.CTkEntry(path_row, textvariable=path_var)
        path_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        def browse_folder():
            folder = filedialog.askdirectory(parent=modal)
            if not folder:
                return

            path_var.set(folder)

            jars = [f for f in os.listdir(folder) if f.lower().endswith(".jar")]
            if jars:
                jar_menu.configure(values=jars)
                jar_var.set(jars[0])

        ctk.CTkButton(
            path_row,
            text="📁",
            width=40,
            command=browse_folder
        ).pack(side="right")


        # ---------- JAR ----------
        ctk.CTkLabel(frame, text="Archivo .jar").pack(anchor="w")
        jar_var = ctk.StringVar(value=cfg.jar if cfg else "server.jar")

        jar_menu = ctk.CTkOptionMenu(
            frame,
            variable=jar_var,
            values=[cfg.jar] if cfg and cfg.jar else ["server.jar"]
        )
        jar_menu.pack(fill="x", pady=5)

        # ---------- RAM MIN ----------
        ram_min_val = ctk.IntVar(value=cfg.ram_min if cfg else 2)
        ram_min_label = ctk.CTkLabel(frame, text=f"RAM mínima: {ram_min_val.get()} GB")
        ram_min_label.pack(anchor="w")

        def update_ram_min(v):
            ram_min_val.set(int(v))
            ram_min_label.configure(text=f"RAM mínima: {int(v)} GB")

        ram_min = ctk.CTkSlider(frame, from_=1, to=32, number_of_steps=31, command=update_ram_min)
        ram_min.set(ram_min_val.get())
        ram_min.pack(fill="x")

        # ---------- RAM MAX ----------
        ram_max_val = ctk.IntVar(value=cfg.ram_max if cfg else 4)
        ram_max_label = ctk.CTkLabel(frame, text=f"RAM máxima: {ram_max_val.get()} GB")
        ram_max_label.pack(anchor="w", pady=(10, 0))

        def update_ram_max(v):
            ram_max_val.set(int(v))
            ram_max_label.configure(text=f"RAM máxima: {int(v)} GB")

        ram_max = ctk.CTkSlider(frame, from_=1, to=32, number_of_steps=31, command=update_ram_max)
        ram_max.set(ram_max_val.get())
        ram_max.pack(fill="x")

        auto_restart_var = ctk.BooleanVar(value=cfg.auto_restart if cfg else False)
        ctk.CTkCheckBox(
            frame,
            text="Reinicio automático",
            variable=auto_restart_var
        ).pack(anchor="w", pady=15)

        def save():
            new_cfg = ServerConfig(
                id=cfg.id if cfg else str(uuid4()),
                name=name.get(),
                jar=jar_var.get(),
                path=path_var.get(),
                ram_min=ram_min_val.get(),
                ram_max=ram_max_val.get(),
                auto_restart=auto_restart_var.get()
            )

            if cfg and cfg.id in self.servers:
                self.servers[cfg.id].config = new_cfg
            else:
                self.servers[new_cfg.id] = ServerRuntime(new_cfg)

            self._save_servers()
            modal.destroy()
            self.show_dashboard()

        ctk.CTkButton(frame, text="💾 Guardar", command=save).pack(pady=20)




# ===================== MAIN =====================
if __name__ == "__main__":
    EsparcraftLauncher().mainloop()
