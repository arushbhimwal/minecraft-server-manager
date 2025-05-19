#!/usr/bin/env python3
import sys
import os
import platform
import json
import subprocess
import requests
import logging
import re
import random
import tempfile
import zipfile
from datetime import datetime
from mcrcon import MCRcon

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QTabWidget, QTextEdit,
    QLineEdit, QFileDialog, QMessageBox, QTreeView, QFileSystemModel,
    QInputDialog, QListWidget, QListWidgetItem, QProgressBar,
    QScrollArea, QSizePolicy, QFormLayout, QDialog, QDialogButtonBox
)
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor, QKeySequence,QShortcut
from PySide6.QtCore import Qt, QThread, Signal, QDir, QStandardPaths
import qdarktheme

# Constants
DEFAULT_USER_AGENT = "MinecraftServerManager/1.0"
PROFILES_FILE = "profiles.json"
SETTINGS_FILE = "settings.json"
MAX_CONSOLE_LINES = 1000
RCON_TIMEOUT = 5

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
                pixmap = QPixmap.fromImage(image)
                self.loaded.emit(self.item_id, pixmap)
        except Exception:
            pixmap = QPixmap(80, 80)
            pixmap.fill(QColor(53, 53, 53))
            painter = QPainter(pixmap)
            painter.setPen(Qt.white)
            painter.drawText(pixmap.rect(), Qt.AlignCenter, "No Image")
            painter.end()
            self.loaded.emit(self.item_id, pixmap)

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
            logging.error(f"Server thread error: {str(e)}")
        finally:
            self.stopped.emit()

    def stop(self):
        self.running = False
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(5000)
                if self.process.poll() is None:
                    self.process.kill()
            except Exception as e:
                logging.error(f"Error stopping process: {str(e)}")

class ModSearchThread(QThread):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, query, mc_version=None, loader_type="forge"):
        super().__init__()
        self.query = query
        self.mc_version = mc_version
        self.loader_type = loader_type

    def run(self):
        try:
            facets = []
            if self.loader_type.lower() == "fabric":
                facets.append(["categories:fabric"])
            elif self.loader_type.lower() == "quilt":
                facets.append(["categories:quilt"])
            else:
                facets.append(["categories:forge"])
            
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
        try:
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                response = requests.get(url, stream=True, timeout=30)
                response.raise_for_status()
                for chunk in response.iter_content(chunk_size=8192):
                    tmp.write(chunk)
                os.replace(tmp.name, path)
            return path
        except Exception as e:
            logging.error(f"Download failed: {str(e)}")
            raise

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

class ServerManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.java_versions = ['8', '11', '17', '21']
        self.loaders = ['Vanilla', 'Paper', 'Spigot', 'Forge', 'Fabric', 'Quilt', 'Mohist']
        self.servers = {}
        self.current_server = None
        self.current_image_loaders = []
        self.api_keys = {'curseforge': ''}
        
        self.init_ui()
        self.load_profiles()
        self.check_java()
        self.check_api_keys()
        self.statusBar().showMessage("Ready")

    def init_ui(self):
        self.setWindowTitle("Minecraft Server Manager")
        self.setGeometry(100, 100, 1200, 800)
        
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # Left panel - Server list
        server_list_panel = QWidget()
        server_list_layout = QVBoxLayout(server_list_panel)
        self.server_list = QListWidget()
        self.server_list.itemClicked.connect(self.select_server)
        server_list_layout.addWidget(QLabel("Servers:"))
        server_list_layout.addWidget(self.server_list)
        
        self.btn_start = QPushButton("Start Server")
        self.btn_start.clicked.connect(self.start_server)
        self.btn_stop = QPushButton("Stop Server")
        self.btn_stop.clicked.connect(self.stop_server)
        self.btn_delete = QPushButton("Delete Server")
        self.btn_delete.clicked.connect(self.delete_server)
        
        server_list_layout.addWidget(self.btn_start)
        server_list_layout.addWidget(self.btn_stop)
        server_list_layout.addWidget(self.btn_delete)
        main_layout.addWidget(server_list_panel, stretch=1)

        # Right panel - Content
        content_panel = QWidget()
        content_layout = QVBoxLayout(content_panel)
        
        # Server creation
        creation_layout = QHBoxLayout()
        self.server_path = QLineEdit()
        self.server_path.setPlaceholderText("Select server directory...")
        btn_browse = QPushButton("Browse")
        btn_browse.clicked.connect(self.select_server_directory)
        btn_create = QPushButton("Create Server")
        btn_create.clicked.connect(self.create_new_server)
        
        creation_layout.addWidget(QLabel("Location:"))
        creation_layout.addWidget(self.server_path)
        creation_layout.addWidget(btn_browse)
        creation_layout.addWidget(btn_create)
        content_layout.addLayout(creation_layout)

        # Configuration
        config_layout = QHBoxLayout()
        self.java_combo = QComboBox()
        self.java_combo.addItems(self.java_versions)
        config_layout.addWidget(QLabel("Java Version:"))
        config_layout.addWidget(self.java_combo)
        
        self.loader_combo = QComboBox()
        self.loader_combo.addItems(self.loaders)
        self.loader_combo.currentTextChanged.connect(self.update_versions)
        config_layout.addWidget(QLabel("Loader:"))
        config_layout.addWidget(self.loader_combo)
        
        self.version_combo = QComboBox()
        config_layout.addWidget(QLabel("MC Version:"))
        config_layout.addWidget(self.version_combo)
        content_layout.addLayout(config_layout)

        # Progress bar
        self.progress = QProgressBar()
        self.progress.hide()
        content_layout.addWidget(self.progress)

        # Tabs
        self.tabs = QTabWidget()
        
        # Console tab
        console_tab = QWidget()
        console_layout = QVBoxLayout(console_tab)
        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        self.command_input = QLineEdit()
        self.command_input.returnPressed.connect(self.send_command)
        console_layout.addWidget(self.console_output)
        console_layout.addWidget(self.command_input)
        self.tabs.addTab(console_tab, "Console")

        # File browser tab
        file_tab = QWidget()
        file_layout = QVBoxLayout(file_tab)
        self.file_model = QFileSystemModel()
        self.file_model.setRootPath(QDir.currentPath())
        self.file_view = QTreeView()
        self.file_view.setModel(self.file_model)
        self.file_view.doubleClicked.connect(self.open_file)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.refresh_file_view)
        file_layout.addWidget(self.file_view)
        file_layout.addWidget(btn_refresh)
        self.tabs.addTab(file_tab, "Files")

        # Mods tab
        mods_tab = QWidget()
        mods_layout = QVBoxLayout(mods_tab)
        mods_layout.addLayout(self.create_url_section("mods"))
        
        mods_platform_layout = QHBoxLayout()
        self.mods_platform_combo = QComboBox()
        self.mods_platform_combo.addItems(["Modrinth", "CurseForge"])
        mods_platform_layout.addWidget(QLabel("Source:"))
        mods_platform_layout.addWidget(self.mods_platform_combo)
        mods_layout.addLayout(mods_platform_layout)
        
        mods_search_layout = QHBoxLayout()
        self.mod_search = QLineEdit()
        self.mod_search.setPlaceholderText("Search mods...")
        btn_mod_search = QPushButton("Search")
        btn_mod_search.clicked.connect(self.safe_mod_search)
        mods_search_layout.addWidget(self.mod_search)
        mods_search_layout.addWidget(btn_mod_search)
        mods_layout.addLayout(mods_search_layout)
        
        mods_scroll = QScrollArea()
        mods_scroll.setWidgetResizable(True)
        self.mod_list_container = QWidget()
        self.mod_list_layout = QVBoxLayout(self.mod_list_container)
        mods_scroll.setWidget(self.mod_list_container)
        mods_layout.addWidget(mods_scroll)
        self.tabs.addTab(mods_tab, "Mods")

        # Plugins tab
        plugins_tab = QWidget()
        plugins_layout = QVBoxLayout(plugins_tab)
        plugins_layout.addLayout(self.create_url_section("plugins"))
        
        plugins_platform_layout = QHBoxLayout()
        self.plugins_platform_combo = QComboBox()
        self.plugins_platform_combo.addItems(["CurseForge", "Modrinth"])
        plugins_platform_layout.addWidget(QLabel("Source:"))
        plugins_platform_layout.addWidget(self.plugins_platform_combo)
        plugins_layout.addLayout(plugins_platform_layout)
        
        plugins_search_layout = QHBoxLayout()
        self.plugin_search = QLineEdit()
        self.plugin_search.setPlaceholderText("Search plugins...")
        btn_plugin_search = QPushButton("Search")
        btn_plugin_search.clicked.connect(self.safe_plugin_search)
        plugins_search_layout.addWidget(self.plugin_search)
        plugins_search_layout.addWidget(btn_plugin_search)
        plugins_layout.addLayout(plugins_search_layout)
        
        plugins_scroll = QScrollArea()
        plugins_scroll.setWidgetResizable(True)
        self.plugin_list_container = QWidget()
        self.plugin_list_layout = QVBoxLayout(self.plugin_list_container)
        plugins_scroll.setWidget(self.plugin_list_container)
        plugins_layout.addWidget(plugins_scroll)
        self.tabs.addTab(plugins_tab, "Plugins")

        # Settings tab
        settings_tab = QWidget()
        settings_layout = QFormLayout(settings_tab)
        self.curseforge_key_input = QLineEdit()
        self.curseforge_key_input.setPlaceholderText("Enter CurseForge API key...")
        settings_layout.addRow("CurseForge API Key:", self.curseforge_key_input)
        btn_save = QPushButton("Save API Key")
        btn_save.clicked.connect(self.save_api_keys)
        settings_layout.addRow(btn_save)
        self.tabs.addTab(settings_tab, "Settings")

        content_layout.addWidget(self.tabs)
        main_layout.addWidget(content_panel, stretch=3)
        
        # Shortcuts
        self.shortcut_send = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.shortcut_send.activated.connect(self.send_command)
        
        self.update_controls()

    def create_url_section(self, target_type):
        url_layout = QVBoxLayout()
        url_input = QTextEdit()
        url_input.setPlaceholderText(f"Paste {target_type} URLs (one per line)...")
        url_input.setMaximumHeight(60)
        
        progress = QProgressBar()
        progress.setFixedHeight(20)
        status = QLabel()
        status.setFixedHeight(18)
        
        install_btn = QPushButton(f"Install {target_type.capitalize()}")
        install_btn.clicked.connect(lambda _, tt=target_type: self.start_url_install(tt))
        
        url_layout.addWidget(QLabel(f"Install from URLs:"))
        url_layout.addWidget(url_input)
        url_layout.addWidget(progress)
        url_layout.addWidget(status)
        url_layout.addWidget(install_btn)
        
        setattr(self, f"{target_type}_url_input", url_input)
        setattr(self, f"{target_type}_progress", progress)
        setattr(self, f"{target_type}_status", status)
        
        return url_layout

    def refresh_file_view(self):
        if self.current_server:
            root_index = self.file_model.index(self.servers[self.current_server]['path'])
            self.file_view.setRootIndex(root_index)

    def validate_server_name(self, name):
        if not name.strip():
            raise ValueError("Server name cannot be empty!")
        if re.search(r'[<>:"/\\|?*]', name):
            raise ValueError("Invalid characters in server name!")
        if name in self.servers:
            raise ValueError("Server name already exists!")

    def load_profiles(self):
        try:
            if os.path.exists(PROFILES_FILE):
                with open(PROFILES_FILE, 'r') as f:
                    self.servers = json.load(f)
                    for server in self.servers.values():
                        server['thread'] = None
                    self.update_server_list()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load profiles: {str(e)}")

    def save_profiles(self):
        try:
            with open(PROFILES_FILE, 'w') as f:
                save_data = {}
                for name, data in self.servers.items():
                    save_data[name] = {k:v for k,v in data.items() if k != 'thread'}
                json.dump(save_data, f, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save profiles: {str(e)}")

    def check_api_keys(self):
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, 'r') as f:
                    self.api_keys = json.load(f)
                    self.curseforge_key_input.setText(self.api_keys.get('curseforge', ''))
        except Exception as e:
            logging.error(f"Settings load failed: {str(e)}")

    def select_server_directory(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select Directory", QDir.homePath(), QFileDialog.ShowDirsOnly
        )
        if path:
            self.server_path.setText(path)

    def create_new_server(self):
        if not self.server_path.text():
            QMessageBox.warning(self, "Error", "Select server directory first!")
            return
        
        name, ok = QInputDialog.getText(self, "Server Name", "Enter server name:")
        if ok and name:
            try:
                self.validate_server_name(name)
                self.setup_server(name)
            except ValueError as e:
                QMessageBox.warning(self, "Error", str(e))

    def setup_server(self, name):
        server_dir = os.path.join(self.server_path.text(), name)
        try:
            os.makedirs(server_dir, exist_ok=True)
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
            logging.error(f"Server creation failed: {str(e)}")
            QMessageBox.critical(self, "Error", f"Server creation failed: {str(e)}")
            if os.path.exists(server_dir):
                self.cleanup_server_dir(server_dir)

    def cleanup_server_dir(self, path):
        try:
            if platform.system() == "Windows":
                subprocess.run(['cmd', '/c', 'rmdir', '/s', '/q', path], check=True)
            else:
                subprocess.run(["rm", "-rf", path], check=True)
        except Exception as e:
            logging.error(f"Cleanup failed: {str(e)}")

    def update_versions(self, loader_name):
        self.version_combo.clear()
        self.progress.show()
        self.statusBar().showMessage("Fetching versions...")
        
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
            
            self.version_combo.addItems(versions)
            self.progress.hide()
            self.statusBar().showMessage("Versions loaded", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to fetch versions: {str(e)}")
            self.progress.hide()
            self.statusBar().showMessage("Version fetch failed", 3000)

    def get_vanilla_url(self, version):
        manifest = requests.get("https://piston-meta.mojang.com/mc/game/version_manifest.json").json()
        for v in manifest['versions']:
            if v['id'] == version and v['type'] == "release":
                version_data = requests.get(v['url']).json()
                return version_data['downloads']['server']['url']
        raise ValueError("Version not found")

    def get_paper_url(self, version):
        builds = requests.get(f"https://api.papermc.io/v2/projects/paper/versions/{version}").json()
        if not builds['builds']:
            raise ValueError("No builds found")
        latest = builds['builds'][-1]
        return f"https://api.papermc.io/v2/projects/paper/versions/{version}/builds/{latest}/downloads/paper-{version}-{latest}.jar"

    def update_server_list(self):
        self.server_list.clear()
        for server_name in self.servers:
            item = QListWidgetItem(server_name)
            status = self.servers[server_name].get('status', 'stopped')
            item.setForeground(Qt.green if status == 'running' else Qt.red)
            self.server_list.addItem(item)

    def select_server(self, item):
        self.current_server = item.text()
        server_data = self.servers[self.current_server]
        self.file_model.setRootPath(server_data['path'])
        self.file_view.setRootIndex(self.file_model.index(server_data['path']))
        self.update_controls()

    def update_controls(self):
        has_selection = self.current_server is not None
        self.btn_start.setEnabled(has_selection)
        self.btn_stop.setEnabled(has_selection)
        self.btn_delete.setEnabled(has_selection)
        
        if has_selection:
            status = self.servers[self.current_server].get('status', 'stopped')
            self.btn_start.setEnabled(status == 'stopped')
            self.btn_stop.setEnabled(status == 'running')

    def check_java(self):
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

    def send_command(self):
        cmd = self.command_input.text()
        self.command_input.clear()
        if not self.current_server or not cmd:
            return
        
        try:
            server = self.servers[self.current_server]
            props_path = os.path.join(server['path'], "server.properties")
            if not os.path.exists(props_path):
                raise FileNotFoundError("server.properties not found")
            
            with open(props_path, 'r') as f:
                for line in f:
                    if line.startswith('rcon.password='):
                        password = line.split('=')[1].strip()
                    if line.startswith('rcon.port='):
                        port = int(line.split('=')[1].strip())
            
            with MCRcon("localhost", password, port, timeout=RCON_TIMEOUT) as mcr:
                response = mcr.command(cmd)
                self.console_output.append(f"> {cmd}\n{response}")
        except ConnectionRefusedError:
            self.console_output.append("RCON Error: Connection refused - check if RCON is enabled")
            self.statusBar().showMessage("RCON Connection Failed", 5000)
        except Exception as e:
            logging.error(f"Command error: {str(e)}")
            self.console_output.append(f"Error: {str(e)}")

    def start_server(self):
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
                self.statusBar().showMessage("Server starting...", 3000)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to start server: {str(e)}")
                self.statusBar().showMessage("Start failed", 3000)

    def handle_server_output(self, message):
        try:
            self.console_output.append(message)
            current_lines = self.console_output.document().lineCount()
            if current_lines > MAX_CONSOLE_LINES:
                cursor = self.console_output.textCursor()
                cursor.movePosition(cursor.Start)
                cursor.select(cursor.LineUnderCursor)
                cursor.removeSelectedText()
        except Exception as e:
            logging.error(f"Output handling error: {str(e)}")

    def handle_server_stop(self):
        if self.current_server:
            self.servers[self.current_server]['status'] = 'stopped'
            self.servers[self.current_server]['thread'] = None
            self.save_profiles()
            self.update_server_list()
            self.statusBar().showMessage("Server stopped", 3000)

    def stop_server(self):
        if self.current_server and self.servers[self.current_server]['status'] == 'running':
            try:
                self.send_command("stop")
                self.servers[self.current_server]['thread'].stop()
                self.servers[self.current_server]['status'] = 'stopping'
                self.save_profiles()
                self.update_server_list()
                self.statusBar().showMessage("Stopping server...", 3000)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to stop server: {str(e)}")
                self.statusBar().showMessage("Stop failed", 3000)

    def delete_server(self):
        if not self.current_server:
            return
        
        reply = QMessageBox.question(
            self,
            "Delete Server",
            f"Permanently delete '{self.current_server}'?",
            QMessageBox.Yes | QMessageBox.No
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
                self.statusBar().showMessage("Server deleted", 3000)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Delete failed: {str(e)}")
                self.statusBar().showMessage("Delete failed", 3000)

    def create_server_properties(self, server_dir):
        password = self.generate_password()
        with open(os.path.join(server_dir, "rcon_password.txt"), 'w') as f:
            f.write(f"RCON Password: {password}\n")
        
        default_props = {
            "server-port": str(random.randint(25000, 30000)),
            "max-players": "20",
            "online-mode": "true",
            "enable-rcon": "true",
            "rcon.password": password,
            "rcon.port": str(random.randint(25000, 30000))
        }
        
        with open(os.path.join(server_dir, "server.properties"), 'w') as f:
            for key, value in default_props.items():
                f.write(f"{key}={value}\n")

    def generate_password(self):
        try:
            return subprocess.check_output(
                ['openssl', 'rand', '-base64', '12'], 
                universal_newlines=True
            ).strip()
        except Exception:
            return str(os.urandom(12).hex())

    def open_file(self, index):
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

    def safe_mod_search(self):
        platform = self.mods_platform_combo.currentText()
        query = self.mod_search.text()
        
        if not query:
            return
        
        self.progress.show()
        self.statusBar().showMessage("Searching mods...")
        
        try:
            if platform == "Modrinth":
                server_version = self.servers[self.current_server]['mc_version'] if self.current_server else None
                self.search_thread = ModSearchThread(query, server_version, self.loader_combo.currentText())
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
            self.statusBar().showMessage("Search failed", 3000)

    def show_mods(self, mods):
        self.progress.hide()
        self.clear_layout(self.mod_list_layout)
        
        try:
            for mod in mods:
                self.create_mod_card(mod)
            self.mod_list_layout.addStretch()
            self.statusBar().showMessage(f"Found {len(mods)} mods", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to display mods: {str(e)}")

    def create_mod_card(self, mod):
        widget = QWidget()
        widget.setFixedHeight(100)
        
        layout = QHBoxLayout(widget)
        icon_label = QLabel()
        icon_label.setFixedSize(80, 80)
        icon_label.setStyleSheet("background-color: #353535;")
        
        text_layout = QVBoxLayout()
        title = QLabel(f"<b>{mod.get('title', mod.get('name'))}</b>")
        title.setStyleSheet("color: white; font-size: 14px;")
        desc = QLabel(mod.get('description', 'No description'))
        desc.setStyleSheet("color: #AAAAAA;")
        
        install_btn = QPushButton("Install")
        install_btn.setStyleSheet("""
            QPushButton { background: #505050; color: white; border: none; padding: 5px; }
            QPushButton:hover { background: #606060; }
        """)
        install_btn.clicked.connect(lambda _, m=mod: self.install_mod(m))
        
        layout.addWidget(icon_label)
        layout.addLayout(text_layout)
        layout.addWidget(install_btn)
        self.mod_list_layout.addWidget(widget)
        
        if mod.get('icon_url'):
            self.load_item_icon(mod['id'], mod['icon_url'], icon_label)

    def install_mod(self, mod):
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
            
            dest_path = os.path.join(mods_dir, filename)
            if os.path.exists(dest_path):
                reply = QMessageBox.question(
                    self, "File Exists", 
                    f"{filename} already exists. Overwrite?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    return
            
            self.download_file(url, dest_path)
            QMessageBox.information(self, "Success", f"Installed {mod.get('title', mod.get('name'))}!")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Install failed: {str(e)}")

    def safe_plugin_search(self):
        """Handle plugin search with error checking"""
        platform = self.plugins_platform_combo.currentText()
        query = self.plugin_search.text()
        
        if not query:
            return
            
        self.progress.show()
        self.statusBar().showMessage("Searching plugins...")
        
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
            self.statusBar().showMessage("Plugin search failed", 3000)

    def _format_cf_plugins(self, cf_plugins):
        """Format CurseForge plugins for display"""
        return [{
            'id': plugin['id'],
            'name': plugin['name'],
            'description': plugin.get('summary', 'No description'),
            'icon_url': plugin['logo']['url'] if plugin.get('logo') else None,
            'versions': plugin['latestFiles']
        } for plugin in cf_plugins]

    def _format_modrinth_plugins(self, modrinth_plugins):
        """Format Modrinth plugins for display"""
        return [{
            'id': plugin['project_id'],
            'name': plugin['title'],
            'description': plugin.get('description', 'No description'),
            'icon_url': plugin.get('icon_url'),
            'versions': plugin['versions']
        } for plugin in modrinth_plugins]

    def save_api_keys(self):
        self.api_keys['curseforge'] = self.curseforge_key_input.text()
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(self.api_keys, f)
            self.statusBar().showMessage("API keys saved", 3000)
        except Exception as e:
            logging.error(f"API key save failed: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to save API keys")

    def closeEvent(self, event):
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