#!/usr/bin/env python3
import sys
import os
import platform
import json
import subprocess
import requests
import logging
import re
from datetime import datetime
from mcrcon import MCRcon

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QTabWidget, QTextEdit,
    QLineEdit, QFileDialog, QMessageBox, QTreeView, QFileSystemModel,
    QInputDialog, QListWidget, QListWidgetItem, QProgressBar,
    QScrollArea, QSizePolicy, QFormLayout, QDialog, QDialogButtonBox
)
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor
from PySide6.QtCore import Qt, QThread, Signal, QDir, QStandardPaths
import qdarktheme

# Constants
DEFAULT_USER_AGENT = "MinecraftServerManager/1.0 (contact@example.com)"
PROFILES_FILE = "profiles.json"
SETTINGS_FILE = "settings.json"

# Logging configuration
logging.basicConfig(
    filename='server_manager.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# --------------------------
# Dialog Classes
# --------------------------

class ApiKeyDialog(QDialog):
    """Dialog for CurseForge API key configuration"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("API Configuration")
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout(self)
        self.curseforge_key = QLineEdit()
        self.curseforge_key.setPlaceholderText("Enter CurseForge API key...")
        
        form = QFormLayout()
        form.addRow("CurseForge API Key:", self.curseforge_key)
        layout.addLayout(form)
        
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

# --------------------------
# Thread Classes
# --------------------------

class ServerThread(QThread):
    output = Signal(str)
    stopped = Signal()

    def __init__(self, command, cwd):
        super().__init__()
        self.command = command
        self.cwd = cwd
        self.process = None
        self.running = False

    def run(self):
        self.running = True
        try:
            self.process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            while self.running:
                output = self.process.stdout.readline()
                if output:
                    self.output.emit(output.strip())
                if self.process.poll() is not None:
                    break
        except Exception as e:
            logger.error(f"Server thread error: {str(e)}")
        finally:
            self.stopped.emit()

    def stop(self):
        self.running = False
        if self.process:
            self.process.terminate()

class ModSearchThread(QThread):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, query, mc_version=None, content_type="mods"):
        super().__init__()
        self.query = query
        self.mc_version = mc_version
        self.content_type = content_type

    def run(self):
        try:
            facets = []
            if self.content_type == "mods":
                facets.append(["categories:forge"])
            elif self.content_type == "plugins":
                facets.append(["categories:bukkit"])
            
            if self.mc_version:
                facets.append([f"versions:{self.mc_version}"])

            headers = {'User-Agent': DEFAULT_USER_AGENT}
            
            response = requests.get(
                f"https://api.modrinth.com/v2/search?query={self.query}&facets={json.dumps(facets)}",
                headers=headers,
                timeout=10
            )
            response.raise_for_status()
            self.finished.emit(response.json()['hits'])
        except Exception as e:
            self.error.emit(str(e))

class CurseForgeSearchThread(QThread):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, query, class_id, api_key):
        super().__init__()
        self.query = query
        self.class_id = class_id
        self.api_key = api_key

    def run(self):
        try:
            headers = {'x-api-key': self.api_key}
            response = requests.post(
                'https://api.curseforge.com/v1/mods/search',
                headers=headers,
                json={
                    'gameId': 432,
                    'searchFilter': self.query,
                    'classId': self.class_id
                },
                timeout=10
            )
            response.raise_for_status()
            self.finished.emit(response.json()['data'])
        except Exception as e:
            self.error.emit(str(e))

class ImageLoaderThread(QThread):
    loaded = Signal(str, QPixmap)

    def __init__(self, url, item_id):
        super().__init__()
        self.url = url
        self.item_id = item_id

    def run(self):
        try:
            response = requests.get(self.url, timeout=10)
            if response.status_code == 200:
                image = QImage()
                image.loadFromData(response.content)
                if not image.isNull():
                    pixmap = QPixmap.fromImage(image)
                    self.loaded.emit(self.item_id, pixmap)
                    return
        except Exception as e:
            logger.warning(f"Image load failed: {str(e)}")
        
        # Fallback image
        pixmap = QPixmap(80, 80)
        pixmap.fill(QColor(53, 53, 53))
        painter = QPainter(pixmap)
        painter.setPen(Qt.white)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "No Image")
        painter.end()
        self.loaded.emit(self.item_id, pixmap)

class UrlInstallThread(QThread):
    progress = Signal(int, str)
    finished = Signal()
    error = Signal(str)

    def __init__(self, urls, server_path, api_key, target_folder):
        super().__init__()
        self.urls = urls
        self.server_path = server_path
        self.api_key = api_key
        self.target_folder = target_folder

    def run(self):
        try:
            total = len(self.urls)
            for index, url in enumerate(self.urls):
                self.progress.emit(int((index/total)*100), f"Processing {url}")
                file_path = self.process_url(url.strip())
                if file_path:
                    self.progress.emit(int(((index+1)/total)*100), f"Installed {os.path.basename(file_path)}")
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

    def process_url(self, url):
        if url.endswith(('.jar', '.zip')):
            return self.direct_download(url)
        if 'modrinth.com' in url:
            return self.handle_modrinth(url)
        if 'curseforge.com' in url:
            return self.handle_curseforge(url)
        raise ValueError("Unsupported URL type")

    def direct_download(self, url):
        save_path = os.path.join(self.server_path, self.target_folder, os.path.basename(url.split('?')[0]))
        return self.download_file(url, save_path)

    def handle_modrinth(self, url):
        match = re.search(r'modrinth\.com/(\w+)/([\w-]+)', url)
        if not match:
            raise ValueError("Invalid Modrinth URL")
        
        project_type = match.group(1)
        project_id = match.group(2)
        
        headers = {'User-Agent': DEFAULT_USER_AGENT}
        
        response = requests.get(
            f'https://api.modrinth.com/v2/project/{project_id}/version',
            headers=headers
        )
        versions = response.json()
        if not versions:
            raise ValueError("No versions available")
        
        server_version = self.get_server_version()
        
        for version in versions:
            if server_version in version['game_versions']:
                file = version['files'][0]
                return self.download_file(file['url'], self.get_save_path(file['filename']))
        
        raise ValueError(f"No version compatible with {server_version}")

    def handle_curseforge(self, url):
        match = re.search(r'curseforge\.com/.+?/(\d+)-', url)
        if not match:
            raise ValueError("Invalid CurseForge URL")
        file_id = match.group(1)

        headers = {'x-api-key': self.api_key}
        response = requests.get(
            f'https://api.curseforge.com/v1/mods/files/{file_id}',
            headers=headers
        )
        file_data = response.json()['data']
        download_url = file_data['downloadUrl']
        
        return self.download_file(download_url, self.get_save_path(file_data['fileName']))

    def download_file(self, url, path):
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return path

    def get_save_path(self, filename):
        save_dir = os.path.join(self.server_path, self.target_folder)
        os.makedirs(save_dir, exist_ok=True)
        return os.path.join(save_dir, filename)

    def get_server_version(self):
        props_path = os.path.join(self.server_path, 'server.properties')
        if os.path.exists(props_path):
            with open(props_path, 'r') as f:
                for line in f:
                    if line.startswith('level-type='):
                        return line.split('=')[1].strip()
        return None

# --------------------------
# Main Application Class
# --------------------------

class ServerManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.java_versions = ['8', '11', '17', '21']
        self.loaders = ['Vanilla', 'Paper', 'Spigot', 'Forge', 'Fabric', 'Quilt', 'Mohist']
        self.servers = {}
        self.current_server = None
        self.current_image_loaders = []
        self.api_keys = {'curseforge': ''}

        self.setup_window()
        self.load_data()
        logger.info("Application started")

    def setup_window(self):
        self.setWindowTitle("Minecraft Server Manager")
        self.setGeometry(100, 100, 1200, 800)
        self.setup_ui()

    def setup_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # Left panel - Server list
        self.setup_server_list_panel(main_layout)
        # Right panel - Content
        self.setup_content_panel(main_layout)

        self.update_controls()

    def setup_server_list_panel(self, main_layout):
        server_list_panel = QWidget()
        server_list_layout = QVBoxLayout(server_list_panel)
        
        self.server_list = QListWidget()
        self.server_list.itemClicked.connect(self.select_server)
        server_list_layout.addWidget(QLabel("Servers:"))
        server_list_layout.addWidget(self.server_list)
        
        # Control buttons
        controls = [
            ("Start Server", self.start_server),
            ("Stop Server", self.stop_server),
            ("Delete Server", self.delete_server)
        ]
        for text, callback in controls:
            btn = QPushButton(text)
            btn.clicked.connect(callback)
            server_list_layout.addWidget(btn)
        
        main_layout.addWidget(server_list_panel, stretch=1)

    def setup_content_panel(self, main_layout):
        content_panel = QWidget()
        content_layout = QVBoxLayout(content_panel)
        
        # Server creation section
        self.setup_server_creation(content_layout)
        # Configuration section
        self.setup_configuration(content_layout)
        # Tabs section
        self.setup_tabs(content_layout)

        main_layout.addWidget(content_panel, stretch=3)

    def setup_server_creation(self, layout):
        creation_layout = QHBoxLayout()
        
        self.server_path = QLineEdit()
        self.server_path.setPlaceholderText("Select server directory...")
        
        components = [
            (QLabel("Location:"), self.server_path),
            (QPushButton("Browse"), self.select_server_directory),
            (QPushButton("Create Server"), self.create_new_server)
        ]
        
        for component in components:
            if isinstance(component[0], QWidget):
                creation_layout.addWidget(component[0])
            else:
                btn = QPushButton(component[0])
                btn.clicked.connect(component[1])
                creation_layout.addWidget(btn)
        
        layout.addLayout(creation_layout)

    def setup_configuration(self, layout):
        config_layout = QHBoxLayout()
        
        components = [
            ("Java Version:", QComboBox(), self.java_versions),
            ("Loader:", QComboBox(), self.loaders, self.update_versions),
            ("MC Version:", QComboBox(), [])  # Initialize with empty list instead of None
        ]
        
        for label, widget, *args in components:
            config_layout.addWidget(QLabel(label))
            if isinstance(widget, QComboBox):
                if args and args[0]:  # Check if items exist before adding
                    widget.addItems(args[0])
                if len(args) > 1:
                    widget.currentTextChanged.connect(args[1])
            config_layout.addWidget(widget)
        
        self.java_combo, self.loader_combo, self.version_combo = [c[1] for c in components]
        layout.addLayout(config_layout)
        layout.addWidget(QProgressBar())
        def setup_tabs(self, layout):
            self.tabs = QTabWidget()
        
        # Setup individual tabs
        tabs = [
            ("Console", self.create_console_tab()),
            ("Files", self.create_file_browser_tab()),
            ("Mods", self.create_mods_tab()),
            ("Plugins", self.create_plugins_tab()),
            ("Settings", self.create_settings_tab())
        ]
        
        for name, widget in tabs:
            self.tabs.addTab(widget, name)
        
        layout.addWidget(self.tabs)

    def create_console_tab(self):
        console_tab = QWidget()
        layout = QVBoxLayout(console_tab)
        
        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        self.command_input = QLineEdit()
        self.command_input.returnPressed.connect(self.send_command)
        
        layout.addWidget(self.console_output)
        layout.addWidget(self.command_input)
        
        return console_tab

    def create_file_browser_tab(self):
        file_tab = QWidget()
        layout = QVBoxLayout(file_tab)
        
        self.file_model = QFileSystemModel()
        self.file_model.setRootPath(QDir.currentPath())
        self.file_view = QTreeView()
        self.file_view.setModel(self.file_model)
        self.file_view.doubleClicked.connect(self.open_file)
        
        layout.addWidget(self.file_view)
        return file_tab

    def create_mods_tab(self):
        mods_tab = QWidget()
        layout = QVBoxLayout(mods_tab)
        
        # URL section
        layout.addLayout(self.create_url_section("mods"))
        # Search section
        layout.addLayout(self.create_search_section("mod"))
        # Results scroll area
        layout.addWidget(self.create_scroll_area("mod"))
        
        return mods_tab

    def create_plugins_tab(self):
        plugins_tab = QWidget()
        layout = QVBoxLayout(plugins_tab)
        
        # URL section
        layout.addLayout(self.create_url_section("plugins"))
        # Search section
        layout.addLayout(self.create_search_section("plugin"))
        # Results scroll area
        layout.addWidget(self.create_scroll_area("plugin"))
        
        return plugins_tab

    def create_settings_tab(self):
        settings_tab = QWidget()
        layout = QFormLayout(settings_tab)
        
        self.curseforge_key_input = QLineEdit()
        self.curseforge_key_input.setPlaceholderText("Enter CurseForge API key...")
        
        layout.addRow("CurseForge API Key:", self.curseforge_key_input)
        layout.addRow(QPushButton("Save API Key", clicked=self.save_api_keys))
        
        return settings_tab

    def create_url_section(self, target_type):
        url_layout = QVBoxLayout()
        url_layout.setContentsMargins(0, 5, 0, 5)
        url_layout.setSpacing(5)
        
        url_layout.addWidget(QLabel(f"Install {target_type.capitalize()} from URLs:"))
        
        url_input = QTextEdit()
        url_input.setPlaceholderText(f"Paste {target_type} URLs (one per line)...")
        url_input.setMaximumHeight(60)
        
        progress = QProgressBar()
        progress.setFixedHeight(20)
        status = QLabel()
        status.setFixedHeight(18)
        
        url_layout.addWidget(url_input)
        url_layout.addWidget(progress)
        url_layout.addWidget(status)
        url_layout.addWidget(QPushButton(f"Install {target_type.capitalize()}", 
                                      clicked=lambda: self.start_url_install(target_type)))
        
        # Store references
        setattr(self, f"{target_type}_url_input", url_input)
        setattr(self, f"{target_type}_progress", progress)
        setattr(self, f"{target_type}_status", status)
        
        return url_layout

    def create_search_section(self, target_type):
        search_layout = QHBoxLayout()
        platform_combo = QComboBox()
        platform_combo.addItems(["Modrinth", "CurseForge"] if target_type == "mod" else ["CurseForge", "Modrinth"])
        
        search_input = QLineEdit()
        search_input.setPlaceholderText(f"Search {target_type}s...")
        search_btn = QPushButton("Search")
        
        search_layout.addWidget(QLabel("Source:"))
        search_layout.addWidget(platform_combo)
        search_layout.addWidget(search_input)
        search_layout.addWidget(search_btn)
        
        # Connect signals
        setattr(self, f"{target_type}s_platform_combo", platform_combo)
        setattr(self, f"{target_type}_search", search_input)
        search_btn.clicked.connect(getattr(self, f"safe_{target_type}_search"))
        
        return search_layout

    def create_scroll_area(self, target_type):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addStretch()
        
        scroll.setWidget(container)
        setattr(self, f"{target_type}_list_layout", layout)
        setattr(self, f"{target_type}_list_container", container)
        
        return scroll

    # --------------------------
    # Core Functionality Methods
    # --------------------------

    def load_data(self):
        self.load_profiles()
        self.check_java()
        self.check_api_keys()

    def load_profiles(self):
        try:
            if os.path.exists(PROFILES_FILE):
                with open(PROFILES_FILE, 'r') as f:
                    self.servers = {name: {**data, 'thread': None} 
                                  for name, data in json.load(f).items()}
                    self.update_server_list()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load profiles: {str(e)}")

    def save_profiles(self):
        try:
            with open(PROFILES_FILE, 'w') as f:
                json.dump({name: {k:v for k,v in data.items() if k != 'thread'} 
                          for name, data in self.servers.items()}, f, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save profiles: {str(e)}")

    def check_api_keys(self):
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, 'r') as f:
                    self.api_keys = json.load(f)
                    self.curseforge_key_input.setText(self.api_keys.get('curseforge', ''))
        except Exception as e:
            logger.error(f"Settings load failed: {str(e)}")

        # --------------------------
    # Server Management Methods
    # --------------------------

    def select_server_directory(self):
        """Handle server directory selection"""
        path = QFileDialog.getExistingDirectory(
            self, "Select Directory", QDir.homePath(), QFileDialog.ShowDirsOnly
        )
        if path:
            self.server_path.setText(path)
            logger.info(f"Selected server directory: {path}")

    def create_new_server(self):
        """Create new server instance"""
        if not self.server_path.text():
            QMessageBox.warning(self, "Error", "Select server directory first!")
            return

        name, ok = QInputDialog.getText(self, "Server Name", "Enter server name:")
        if ok and name:
            self.setup_server(name)

    def setup_server(self, name):
        """Configure new server instance"""
        server_dir = os.path.join(self.server_path.text(), name)
        try:
            os.makedirs(server_dir, exist_ok=True)
            logger.info(f"Creating server: {name} at {server_dir}")

            loader = self.loader_combo.currentText()
            version = self.version_combo.currentText()
            
            self.servers[name] = {
                "path": server_dir,
                "status": "stopped",
                "max_ram": "2G",
                "mc_version": version,
                "thread": None
            }

            # Download server jar
            if loader == "Vanilla":
                url = self.get_vanilla_url(version)
            elif loader == "Paper":
                url = self.get_paper_url(version)
            else:
                raise ValueError("Unsupported loader")

            self.download_file(url, os.path.join(server_dir, "server.jar"))
            self.create_server_properties(server_dir)
            
            # Create eula.txt
            with open(os.path.join(server_dir, "eula.txt"), 'w') as f:
                f.write("eula=true\n")
            
            self.save_profiles()
            self.update_server_list()
            QMessageBox.information(self, "Success", f"Server '{name}' created!")

        except Exception as e:
            logger.error(f"Server creation failed: {str(e)}")
            QMessageBox.critical(self, "Error", f"Server creation failed: {str(e)}")
            if os.path.exists(server_dir):
                self.cleanup_server_dir(server_dir)

    def cleanup_server_dir(self, path):
        """Remove failed server directory"""
        try:
            if platform.system() == "Windows":
                subprocess.run(['cmd', '/c', 'rmdir', '/s', '/q', path], check=True)
            else:
                subprocess.run(["rm", "-rf", path], check=True)
        except Exception as e:
            logger.error(f"Cleanup failed: {str(e)}")

    # --------------------------
    # Version Management
    # --------------------------

    def update_versions(self, loader_name):
        """Fetch available Minecraft versions for selected loader"""
        self.version_combo.clear()
        self.progress.show()
        
        try:
            if loader_name == "Vanilla":
                response = requests.get("https://piston-meta.mojang.com/mc/game/version_manifest.json", timeout=10)
                versions = [v['id'] for v in response.json()['versions'] if v['type'] == 'release']
            elif loader_name == "Paper":
                response = requests.get("https://api.papermc.io/v2/projects/paper", timeout=10)
                versions = response.json()['versions'][::-1]
            elif loader_name == "Fabric":
                response = requests.get("https://meta.fabricmc.net/v2/versions/game", timeout=10)
                versions = [v['version'] for v in response.json()]
            else:
                versions = ["Version selection not implemented"]
            
            self.version_combo.addItems(versions[:20])
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to fetch versions: {str(e)}")
        finally:
            self.progress.hide()

    def get_vanilla_url(self, version):
        """Get Vanilla server download URL"""
        manifest = requests.get("https://piston-meta.mojang.com/mc/game/version_manifest.json").json()
        for v in manifest['versions']:
            if v['id'] == version and v['type'] == "release":
                version_data = requests.get(v['url']).json()
                return version_data['downloads']['server']['url']
        raise ValueError("Version not found")

    def get_paper_url(self, version):
        """Get PaperMC server download URL"""
        builds = requests.get(f"https://api.papermc.io/v2/projects/paper/versions/{version}").json()
        if not builds['builds']:
            raise ValueError("No builds found")
        latest = builds['builds'][-1]
        return f"https://api.papermc.io/v2/projects/paper/versions/{version}/builds/{latest}/downloads/paper-{version}-{latest}.jar"

    # --------------------------
    # File Operations
    # --------------------------

    def download_file(self, url, path):
        """Download a file with progress tracking"""
        try:
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()
            with open(path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info(f"Downloaded {os.path.basename(path)}")
        except Exception as e:
            logger.error(f"Download failed: {str(e)}")
            raise

    def create_server_properties(self, server_dir):
        """Create default server.properties file"""
        try:
            default_props = {
                "server-port": "25565",
                "max-players": "20",
                "online-mode": "true",
                "enable-rcon": "true",
                "rcon.password": self.generate_password(),
                "rcon.port": "25575"
            }
            with open(os.path.join(server_dir, "server.properties"), 'w') as f:
                for key, value in default_props.items():
                    f.write(f"{key}={value}\n")
        except Exception as e:
            logger.error(f"Failed to create server.properties: {str(e)}")
            raise

    def generate_password(self):
        """Generate secure RCON password"""
        try:
            return subprocess.check_output(
                ['openssl', 'rand', '-base64', '12'], 
                universal_newlines=True
            ).strip()
        except Exception:
            return str(os.urandom(12).hex())

    def open_file(self, index):
        """Open selected file in default application"""
        path = self.file_model.filePath(index)
        if os.path.isfile(path):
            try:
                if platform.system() == "Windows":
                    os.startfile(path)
                else:
                    opener = "open" if platform.system() == "Darwin" else "xdg-open"
                    subprocess.run([opener, path])
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not open file: {str(e)}")

    # --------------------------
    # Server Controls
    # --------------------------

    def update_server_list(self):
        """Refresh server list display"""
        self.server_list.clear()
        for server_name in self.servers:
            item = QListWidgetItem(server_name)
            status = self.servers[server_name].get('status', 'stopped')
            item.setForeground(Qt.green if status == 'running' else Qt.red)
            self.server_list.addItem(item)

    def select_server(self, item):
        """Handle server selection"""
        self.current_server = item.text()
        server_data = self.servers[self.current_server]
        self.file_model.setRootPath(server_data['path'])
        self.file_view.setRootIndex(self.file_model.index(server_data['path']))
        self.update_controls()

    def update_controls(self):
        """Update UI control states based on server status"""
        has_selection = self.current_server is not None
        self.btn_start.setEnabled(has_selection)
        self.btn_stop.setEnabled(has_selection)
        self.btn_delete.setEnabled(has_selection)
        
        if has_selection:
            status = self.servers[self.current_server].get('status', 'stopped')
            self.btn_start.setEnabled(status == 'stopped')
            self.btn_stop.setEnabled(status == 'running')

    def check_java(self):
        """Verify Java installation"""
        try:
            result = subprocess.run(
                ['java', '-version'],
                capture_output=True,
                text=True,
                check=True
            )
            version_info = result.stderr.splitlines()[0]
            detected_version = version_info.split()[2].strip('\"').split('.')[0]
            if detected_version in self.java_versions:
                self.java_combo.setCurrentText(detected_version)
        except (subprocess.CalledProcessError, FileNotFoundError):
            QMessageBox.warning(self, "Java Not Found", "Java runtime not detected!")

    # --------------------------
    # Server Operations
    # --------------------------

    def send_command(self):
        """Send command to server via RCON"""
        cmd = self.command_input.text()
        self.command_input.clear()
        if not self.current_server:
            return
            
        try:
            server = self.servers[self.current_server]
            with MCRcon("localhost", "password", 25575) as mcr:
                response = mcr.command(cmd)
                self.console_output.append(f"> {cmd}\n{response}")
        except Exception as e:
            self.console_output.append(f"RCON error: {str(e)}")

    def start_server(self):
        """Start selected server"""
        if not self.current_server:
            return
            
        server = self.servers[self.current_server]
        if server['status'] == 'stopped':
            try:
                java_path = "java.exe" if platform.system() == "Windows" else "java"
                command = [
                    java_path,
                    f"-Xmx{server.get('max_ram', '2G')}",
                    "-jar",
                    "server.jar",
                    "nogui"
                ]
                
                server['thread'] = ServerThread(command, server['path'])
                server['thread'].output.connect(self.handle_server_output)
                server['thread'].stopped.connect(self.handle_server_stop)
                server['thread'].start()
                server['status'] = 'running'
                self.save_profiles()
                self.update_server_list()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to start server: {str(e)}")

    def stop_server(self):
        """Stop running server"""
        if self.current_server and self.servers[self.current_server]['status'] == 'running':
            try:
                self.send_command("stop")
                self.servers[self.current_server]['thread'].stop()
                self.servers[self.current_server]['status'] = 'stopping'
                self.save_profiles()
                self.update_server_list()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to stop server: {str(e)}")

    def handle_server_output(self, message):
        """Handle server console output"""
        self.console_output.append(message)

    def handle_server_stop(self):
        """Handle server shutdown completion"""
        if self.current_server:
            self.servers[self.current_server]['status'] = 'stopped'
            self.servers[self.current_server]['thread'] = None
            self.save_profiles()
            self.update_server_list()

    def delete_server(self):
        """Delete selected server"""
        if not self.current_server:
            return
            
        reply = QMessageBox.question(
            self,
            "Delete Server",
            f"Delete '{self.current_server}'?",
            QMessageBox.Yes | QDialogButtonBox.No
        )
        
        if reply == QMessageBox.Yes:
            server_path = self.servers[self.current_server]['path']
            try:
                if os.path.exists(server_path):
                    if platform.system() == "Windows":
                        subprocess.run(['cmd', '/c', 'rmdir', '/s', '/q', server_path], check=True)
                    else:
                        subprocess.run(["rm", "-rf", server_path], check=True)
                del self.servers[self.current_server]
                self.current_server = None
                self.save_profiles()
                self.update_server_list()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Delete failed: {str(e)}")

    # --------------------------
    # Mod/Plugin Management
    # --------------------------

    def safe_mod_search(self):
        """Handle mod search with error checking"""
        platform = self.mods_platform_combo.currentText()
        query = self.mod_search.text()
        
        if not query:
            return
            
        self.progress.show()
        
        try:
            if platform == "Modrinth":
                server_version = self.servers[self.current_server]['mc_version'] if self.current_server else None
                self.search_thread = ModSearchThread(query, server_version, "mods")
                self.search_thread.finished.connect(self.show_mods)
            elif platform == "CurseForge":
                if not self.api_keys.get('curseforge'):
                    QMessageBox.warning(self, "Error", "CurseForge API key required!")
                    return
                self.search_thread = CurseForgeSearchThread(query, 6, self.api_keys['curseforge'])
                self.search_thread.finished.connect(lambda data: self.show_mods(self._format_cf_mods(data)))
            
            self.search_thread.error.connect(self.show_search_error)
            self.search_thread.start()
        except Exception as e:
            self.progress.hide()
            QMessageBox.critical(self, "Error", f"Search failed: {str(e)}")

    def safe_plugin_search(self):
        """Handle plugin search with error checking"""
        platform = self.plugins_platform_combo.currentText()
        query = self.plugin_search.text()
        
        if not query:
            return
            
        self.progress.show()
        
        try:
            if platform == "CurseForge":
                if not self.api_keys.get('curseforge'):
                    QMessageBox.warning(self, "Error", "CurseForge API key required!")
                    return
                self.search_thread = CurseForgeSearchThread(query, 5, self.api_keys['curseforge'])
                self.search_thread.finished.connect(lambda data: self.show_plugins(self._format_cf_plugins(data)))
            elif platform == "Modrinth":
                server_version = self.servers[self.current_server]['mc_version'] if self.current_server else None
                self.search_thread = ModSearchThread(query, server_version, "plugins")
                self.search_thread.finished.connect(lambda data: self.show_plugins(self._format_modrinth_plugins(data)))
            
            self.search_thread.error.connect(self.show_search_error)
            self.search_thread.start()
        except Exception as e:
            self.progress.hide()
            QMessageBox.critical(self, "Error", f"Search failed: {str(e)}")

    def _format_cf_mods(self, cf_mods):
        """Format CurseForge mod results for display"""
        return [{
            'id': mod['id'],
            'title': mod['name'],
            'description': mod.get('summary', 'No description'),
            'icon_url': mod['logo']['url'] if mod.get('logo') else None,
            'versions': mod['latestFiles']
        } for mod in cf_mods]

    def _format_modrinth_plugins(self, modrinth_plugins):
        """Format Modrinth plugin results for display"""
        return [{
            'id': plugin['project_id'],
            'name': plugin['title'],
            'description': plugin.get('description', 'No description'),
            'icon_url': plugin.get('icon_url'),
            'versions': plugin['versions']
        } for plugin in modrinth_plugins]

    def _format_cf_plugins(self, cf_plugins):
        """Format CurseForge plugin results for display"""
        return [{
            'id': plugin['id'],
            'name': plugin['name'],
            'description': plugin.get('summary', 'No description'),
            'icon_url': plugin['logo']['url'] if plugin.get('logo') else None,
            'versions': plugin['latestFiles']
        } for plugin in cf_plugins]

    def show_mods(self, mods):
        """Display mod search results"""
        self.progress.hide()
        self.clear_layout(self.mod_list_layout)
        
        try:
            for mod in mods:
                self.create_mod_card(mod)
            self.mod_list_layout.addStretch()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to display mods: {str(e)}")

    def show_plugins(self, plugins):
        """Display plugin search results"""
        self.progress.hide()
        self.clear_layout(self.plugin_list_layout)
        
        try:
            for plugin in plugins:
                self.create_plugin_card(plugin)
            self.plugin_list_layout.addStretch()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to display plugins: {str(e)}")

    def clear_layout(self, layout):
        """Clear a QLayout"""
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def create_mod_card(self, mod):
        """Create UI card for a mod"""
        widget = QWidget()
        widget.setFixedHeight(100)
        
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Icon
        icon_label = QLabel()
        icon_label.setFixedSize(80, 80)
        icon_label.setStyleSheet("background-color: #353535;")
        layout.addWidget(icon_label)
        
        # Text info
        text_layout = QVBoxLayout()
        title = QLabel(f"<b>{mod.get('title', mod.get('name'))}</b>")
        title.setStyleSheet("color: white; font-size: 14px;")
        desc = QLabel(mod.get('description', 'No description'))
        desc.setStyleSheet("color: #AAAAAA;")
        text_layout.addWidget(title)
        text_layout.addWidget(desc)
        
        # Install button
        install_btn = QPushButton("Install")
        install_btn.setStyleSheet("""
            QPushButton {
                background: #505050;
                color: white;
                border: none;
                padding: 5px;
                min-width: 80px;
            }
            QPushButton:hover { background: #606060; }
        """)
        install_btn.clicked.connect(lambda _, m=mod: self.install_mod(m))
        
        layout.addLayout(text_layout)
        layout.addWidget(install_btn)
        self.mod_list_layout.addWidget(widget)
        
        # Load icon
        if mod.get('icon_url'):
            self.load_item_icon(mod['id'], mod['icon_url'], icon_label)

    def create_plugin_card(self, plugin):
        """Create UI card for a plugin"""
        widget = QWidget()
        widget.setFixedHeight(100)
        
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Icon
        icon_label = QLabel()
        icon_label.setFixedSize(80, 80)
        icon_label.setStyleSheet("background-color: #353535;")
        layout.addWidget(icon_label)
        
        # Text info
        text_layout = QVBoxLayout()
        title = QLabel(f"<b>{plugin.get('name')}</b>")
        title.setStyleSheet("color: white; font-size: 14px;")
        desc = QLabel(plugin.get('description', 'No description'))
        desc.setStyleSheet("color: #AAAAAA;")
        text_layout.addWidget(title)
        text_layout.addWidget(desc)
        
        # Install button
        install_btn = QPushButton("Install")
        install_btn.setStyleSheet("""
            QPushButton {
                background: #505050;
                color: white;
                border: none;
                padding: 5px;
                min-width: 80px;
            }
            QPushButton:hover { background: #606060; }
        """)
        install_btn.clicked.connect(lambda _, p=plugin: self.install_plugin(p))
        
        layout.addLayout(text_layout)
        layout.addWidget(install_btn)
        self.plugin_list_layout.addWidget(widget)
        
        # Load icon
        if plugin.get('icon_url'):
            self.load_item_icon(plugin['id'], plugin['icon_url'], icon_label)

    def load_item_icon(self, item_id, url, target_label):
        """Load and display item icon asynchronously"""
        loader = ImageLoaderThread(url, item_id)
        loader.loaded.connect(lambda item_id, pixmap: self.update_item_icon(item_id, pixmap, target_label))
        self.current_image_loaders.append(loader)
        loader.start()

    def update_item_icon(self, item_id, pixmap, target_label):
        """Update displayed icon for an item"""
        target_label.setPixmap(pixmap.scaled(
            80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))

    def install_mod(self, mod):
        """Install selected mod"""
        if not self.current_server:
            QMessageBox.warning(self, "Error", "Select a server first!")
            return

        try:
            platform = self.mods_platform_combo.currentText()
            server_path = self.servers[self.current_server]['path']
            mods_dir = os.path.join(server_path, "mods")
            os.makedirs(mods_dir, exist_ok=True)

            if platform == "Modrinth":
                version = mod['versions'][0]
                file = version['files'][0]
                url = file['url']
                filename = file['filename']
            elif platform == "CurseForge":
                file = mod['versions'][0]
                url = file['downloadUrl']
                filename = file['fileName']
            
            self.download_file(url, os.path.join(mods_dir, filename))
            QMessageBox.information(self, "Success", f"Installed {mod.get('title', mod.get('name'))}!")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Install failed: {str(e)}")

    def install_plugin(self, plugin):
        """Install selected plugin"""
        if not self.current_server:
            QMessageBox.warning(self, "Error", "Select a server first!")
            return

        try:
            platform = self.plugins_platform_combo.currentText()
            server_path = self.servers[self.current_server]['path']
            plugins_dir = os.path.join(server_path, "plugins")
            os.makedirs(plugins_dir, exist_ok=True)

            if platform == "Modrinth":
                version = plugin['versions'][0]
                file = version['files'][0]
                url = file['url']
                filename = file['filename']
            elif platform == "CurseForge":
                file = plugin['versions'][0]
                url = file['downloadUrl']
                filename = file['fileName']
            
            self.download_file(url, os.path.join(plugins_dir, filename))
            QMessageBox.information(self, "Success", f"Installed {plugin['name']}!")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Install failed: {str(e)}")

    # --------------------------
    # URL Installation
    # --------------------------

    def start_url_install(self, target_type):
        """Start URL-based installation process"""
        if not self.current_server:
            QMessageBox.warning(self, "Error", "Select a server first!")
            return
            
        urls = getattr(self, f"{target_type}_url_input").toPlainText().split('\n')
        if not urls:
            QMessageBox.warning(self, "Error", "Enter at least one URL!")
            return
            
        server_path = self.servers[self.current_server]['path']
        progress_bar = getattr(self, f"{target_type}_progress")
        status_label = getattr(self, f"{target_type}_status")
        
        self.url_thread = UrlInstallThread(
            urls,
            server_path,
            self.api_keys.get('curseforge', ''),
            target_type
        )
        self.url_thread.progress.connect(
            lambda v, m: self.update_url_progress(v, m, target_type)
        )
        self.url_thread.finished.connect(
            lambda: self.url_install_finished(target_type)
        )
        self.url_thread.error.connect(
            lambda m: self.url_install_error(m, target_type)
        )
        self.url_thread.start()
        
        progress_bar.show()
        status_label.show()
        progress_bar.setValue(0)
        status_label.setText("Starting installation...")

    def update_url_progress(self, value, message, target_type):
        """Update URL installation progress"""
        getattr(self, f"{target_type}_progress").setValue(value)
        getattr(self, f"{target_type}_status").setText(message)

    def url_install_finished(self, target_type):
        """Handle successful URL installation"""
        getattr(self, f"{target_type}_progress").hide()
        getattr(self, f"{target_type}_status").hide()
        QMessageBox.information(self, "Success", f"{target_type.capitalize()} installed successfully!")

    def url_install_error(self, message, target_type):
        """Handle URL installation errors"""
        getattr(self, f"{target_type}_progress").hide()
        getattr(self, f"{target_type}_status").hide()
        QMessageBox.critical(self, "Error", message)

    # --------------------------
    # Utility Methods
    # --------------------------

    def show_search_error(self, message):
        """Display search error message"""
        self.progress.hide()
        QMessageBox.critical(self, "Error", f"Search failed: {message}")

    def show_api_key_dialog(self):
        """Show API key configuration dialog"""
        dialog = ApiKeyDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.api_keys['curseforge'] = dialog.curseforge_key.text()
            self.save_api_keys()
            self.curseforge_key_input.setText(self.api_keys['curseforge'])
        else:
            QMessageBox.warning(self, "API Key Required", "CurseForge functionality will be limited!")

    def save_api_keys(self):
        """Save API keys to settings file"""
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump({
                    'curseforge': self.api_keys['curseforge']
                }, f)
        except Exception as e:
            logger.error(f"API key save failed: {str(e)}")

    def closeEvent(self, event):
        """Handle application shutdown"""
        for loader in self.current_image_loaders:
            if loader.isRunning():
                loader.quit()
        self.save_profiles()
        self.save_api_keys()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    qdarktheme.setup_theme()
    window = ServerManager()
    window.show()
    sys.exit(app.exec())