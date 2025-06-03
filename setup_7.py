#!/usr/bin/env python3
import sys
import os
import platform
import json
import subprocess
import requests
import logging
import re
import time
import random
import tempfile
import zipfile
from datetime import datetime
from functools import lru_cache
from cryptography.fernet import Fernet
from mcrcon import MCRcon

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QTabWidget, QTextEdit,
    QLineEdit, QFileDialog, QMessageBox, QTreeView, QFileSystemModel,
    QInputDialog, QListWidget, QListWidgetItem, QProgressBar,
    QScrollArea, QSizePolicy, QFormLayout, QDialog, QDialogButtonBox
)
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor, QKeySequence, QShortcut
from PySide6.QtCore import Qt, QThread, Signal, QDir, QStandardPaths
import qdarktheme

#region Constants
class Constants:
    HEADERS = {
        "DEFAULT_USER_AGENT": "MinecraftServerManager/1.0 (+https://github.com/arushbhimwal/minecraft-server-manager)"
        }
    SERVER_STATUS = {
        "STOPPED": "stopped", "RUNNING": "running", "STARTING": "starting"
        }
    FILES = {
        "PROFILES": "profiles.json",
        "SETTINGS": "settings.json",
        "SERVER_JAR": "server.jar",
        "SERVER_PROPERTIES": "server.properties",
        "EULA_FILE": "eula.txt"
        }
    API_ENDPOINTS = {
        "VANILLA_MANIFEST": "https://piston-meta.mojang.com/mc/game/version_manifest.json",
        "PAPER_VERSIONS": "https://api.papermc.io/v2/projects/paper",
        "MODRINTH_VERSIONS": "https://api.modrinth.com/v2/tag/game_version",
        "FABRIC_VERSIONS": "https://meta.fabricmc.net/v2/versions/game"
        }
    MAX_CONSOLE_LINES = 1000
    RCON_TIMEOUT = 5
    BACKUP_DIR = "backups"
    SECURITY_KEY_FILE = ".encryption.key"
#endregion

#region Security
class SecureSettings:
    def __init__(self):
        self.cipher = self._get_cipher()
        
    def _get_cipher(self):
        if not os.path.exists(Constants.SECURITY_KEY_FILE):
            key = Fernet.generate_key()
            with open(Constants.SECURITY_KEY_FILE, 'wb') as f: f.write(key)
        else:
            with open(Constants.SECURITY_KEY_FILE, 'rb') as f: key = f.read()
        return Fernet(key)
    
    def encrypt(self, data):
        return self.cipher.encrypt(data.encode()).decode()
    
    def decrypt(self, encrypted_data):
        return self.cipher.decrypt(encrypted_data.encode()).decode()
#endregion

#region API Handlers
class ModrinthAPI:
    @lru_cache(maxsize=100)
    def get_versions(self, project_id):
        response = requests.get(f'https://api.modrinth.com/v2/project/{project_id}/version')
        return response.json()

class CurseForgeAPI:
    @lru_cache(maxsize=100)
    def get_file_info(self, file_id, api_key):
        response = requests.get(f'https://api.curseforge.com/v1/mods/files/{file_id}',
            headers={'x-api-key': api_key})
        return response.json()
#endregion

#region Threads
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

    def __del__(self):
        if self.isRunning():
            self.quit()
            self.wait(5000)

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

            headers = {'User-Agent': Constants.HEADERS["DEFAULT_USER_AGENT"]}
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
                json={'gameId': 432, 'searchFilter': self.query, 'classId': self.class_id},
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
        
        headers = {'User-Agent': Constants.HEADERS["DEFAULT_USER_AGENT"]}
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
        max_retries = 3
        retry_delay = 1  # seconds
        temp_file = None

        try:
            for attempt in range(max_retries):
                try:
                    # Create temp file in the same directory to avoid cross-device issues
                    temp_dir = os.path.dirname(path)
                    temp_file = tempfile.NamedTemporaryFile(
                        dir=temp_dir,
                        delete=False,
                        prefix="mc_temp_",
                        suffix=".download"
                    )

                    print(f"[DEBUG] Downloading {url} to temp file: {temp_file.name}")

                    # Stream download
                    response = requests.get(url, stream=True, timeout=30)
                    response.raise_for_status()

                    # Write content
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:  # filter out keep-alive chunks
                            temp_file.write(chunk)

                    # Close before move to release file handle
                    temp_file.close()
                    print(f"[DEBUG] Temp file closed. Moving {temp_file.name} -> {path}")

                    # Atomic replace (works across volumes on Windows)
                    os.replace(temp_file.name, path)
                    print("[DEBUG] File move successful")
                    return path

                except PermissionError as e:
                    print(f"[ERROR] File access error (attempt {attempt + 1}/{max_retries}): {str(e)}")
                    if attempt < max_retries - 1:
                        # Cleanup and retry
                        if temp_file and not temp_file.closed:
                            temp_file.close()
                        if os.path.exists(temp_file.name):
                            os.unlink(temp_file.name)
                        time.sleep(retry_delay * (attempt + 1))
                        continue
                    raise

        finally:
            # Final cleanup if still exists
            if temp_file and os.path.exists(temp_file.name):
                try:
                    os.unlink(temp_file.name)
                except Exception as cleanup_error:
                    print(f"[WARNING] Temp file cleanup failed: {str(cleanup_error)}")

        return None

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

#endregion

#region Backup System
class BackupManager:
    def __init__(self, server_path):
        self.server_path = server_path
        self.backup_dir = os.path.join(server_path, Constants.BACKUP_DIR)
        
    def create_backup(self):
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = os.path.join(self.backup_dir, f"backup-{timestamp}.zip")
        os.makedirs(self.backup_dir, exist_ok=True)
        
        try:
            with zipfile.ZipFile(backup_path, 'w') as zipf:
                for root, _, files in os.walk(self.server_path):
                    for file in files:
                        if Constants.BACKUP_DIR not in root:
                            full_path = os.path.join(root, file)
                            arcname = os.path.relpath(full_path, self.server_path)
                            zipf.write(full_path, arcname)
            return backup_path
        except Exception as e:
            logging.error(f"Backup failed: {str(e)}")
            raise
#endregion

#region Main Application
class ServerManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.java_versions = ['8', '11', '17', '21']
        self.loaders = ['Vanilla', 'Paper', 'Spigot', 'Forge', 'Fabric', 'Quilt', 'Mohist']
        self.servers = {}
        self.current_server = None
        self.current_image_loaders = []
        self.api_keys = {'curseforge': ''}
        self.secure_settings = SecureSettings()
        
        # Initialize UI and components
        self.init_ui()
        self.load_profiles()
        self.check_java()
        self.check_api_keys()
        self.setup_logging()
        self.setStyleSheet(self.get_style_sheet())
        self.statusBar().showMessage("Ready")

    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[logging.FileHandler('server_manager.log', encoding='utf-8')]
        )

    def get_style_sheet(self):
        return """
            QTextEdit, QLineEdit, QComboBox { padding: 3px; margin: 1px; }
            QPushButton { min-height: 25px; margin: 2px; }
            QTabWidget::pane { border: 1px solid #444; margin: 2px; }
            QProgressBar { text-align: center; }
            QLabel { margin: 2px; }
        """

    def init_ui(self):
        self.setWindowTitle("Minecraft Server Manager")
        self.setGeometry(100, 100, 1200, 800)
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # Left Panel
        server_list_panel = QWidget()
        server_list_layout = QVBoxLayout(server_list_panel)
        self.setup_server_list(server_list_layout)
        main_layout.addWidget(server_list_panel, stretch=1)

        # Right Panel
        content_panel = QWidget()
        content_layout = QVBoxLayout(content_panel)
        self.setup_creation_form(content_layout)
        self.setup_tabs(content_layout)
        main_layout.addWidget(content_panel, stretch=3)

        # Shortcuts
        self.setup_shortcuts()

    def setup_server_list(self, layout):
        self.server_list = QListWidget()
        self.server_list.itemClicked.connect(self.select_server)
        layout.addWidget(QLabel("Servers:"))
        layout.addWidget(self.server_list)
        
        buttons = [
            ("Start Server", self.start_server),
            ("Stop Server", self.stop_server),
            ("Create Backup", self.create_backup),
            ("Delete Server", self.delete_server)
        ]
        
        for text, handler in buttons:
            btn = QPushButton(text)
            btn.clicked.connect(handler)
            layout.addWidget(btn)

    def setup_creation_form(self, layout):
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
        layout.addLayout(creation_layout)

        # Configuration
        config_layout = QHBoxLayout()
        self.java_combo = QComboBox()
        self.java_combo.addItems(self.java_versions)
        
        self.loader_combo = QComboBox()
        self.loader_combo.addItems(self.loaders)
        self.loader_combo.currentTextChanged.connect(self.update_versions)
        
        self.version_combo = QComboBox()
        
        config_layout.addWidget(QLabel("Java:"))
        config_layout.addWidget(self.java_combo)
        config_layout.addWidget(QLabel("Loader:"))
        config_layout.addWidget(self.loader_combo)
        config_layout.addWidget(QLabel("MC Version:"))
        config_layout.addWidget(self.version_combo)
        layout.addLayout(config_layout)

        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
#endregion

#region Tab Components
    def setup_tabs(self, layout):
        self.tabs = QTabWidget()
        
        # Console Tab
        console_tab = QWidget()
        self.setup_console_tab(console_tab)
        self.tabs.addTab(console_tab, "Console")

        # File Browser Tab
        file_tab = QWidget()
        self.setup_file_tab(file_tab)
        self.tabs.addTab(file_tab, "Files")

        # Mods Tab
        mods_tab = QWidget()
        self.setup_mods_tab(mods_tab)
        self.tabs.addTab(mods_tab, "Mods")

        # Plugins Tab
        plugins_tab = QWidget()
        self.setup_plugins_tab(plugins_tab)
        self.tabs.addTab(plugins_tab, "Plugins")

        # Settings Tab
        settings_tab = QWidget()
        self.setup_settings_tab(settings_tab)
        self.tabs.addTab(settings_tab, "Settings")

        layout.addWidget(self.tabs)

    def setup_console_tab(self, parent):
        layout = QVBoxLayout(parent)
        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        self.command_input = QLineEdit()
        self.command_input.returnPressed.connect(self.send_command)
        layout.addWidget(self.console_output)
        layout.addWidget(self.command_input)

    def setup_file_tab(self, parent):
        layout = QVBoxLayout(parent)
        self.file_model = QFileSystemModel()
        self.file_model.setRootPath(QDir.currentPath())
        self.file_view = QTreeView()
        self.file_view.setModel(self.file_model)
        self.file_view.doubleClicked.connect(self.open_file)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.refresh_file_view)
        layout.addWidget(self.file_view)
        layout.addWidget(btn_refresh)

    def setup_mods_tab(self, parent):
        layout = QVBoxLayout(parent)
        layout.addLayout(self.create_url_section("mods"))
        
        # Platform Selection
        platform_layout = QHBoxLayout()
        self.mods_platform_combo = QComboBox()
        self.mods_platform_combo.addItems(["Modrinth", "CurseForge"])
        platform_layout.addWidget(QLabel("Source:"))
        platform_layout.addWidget(self.mods_platform_combo)
        layout.addLayout(platform_layout)

        # Search Section
        search_layout = QHBoxLayout()
        self.mod_search = QLineEdit()
        self.mod_search.setPlaceholderText("Search mods...")
        btn_search = QPushButton("Search")
        btn_search.clicked.connect(self.safe_mod_search)
        search_layout.addWidget(self.mod_search)
        search_layout.addWidget(btn_search)
        layout.addLayout(search_layout)

        # Results List
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.mod_list_container = QWidget()
        self.mod_list_layout = QVBoxLayout(self.mod_list_container)
        scroll.setWidget(self.mod_list_container)
        layout.addWidget(scroll)

    def setup_plugins_tab(self, parent):
        layout = QVBoxLayout(parent)
        layout.addLayout(self.create_url_section("plugins"))
        
        # Platform Selection
        platform_layout = QHBoxLayout()
        self.plugins_platform_combo = QComboBox()
        self.plugins_platform_combo.addItems(["CurseForge", "Modrinth"])
        platform_layout.addWidget(QLabel("Source:"))
        platform_layout.addWidget(self.plugins_platform_combo)
        layout.addLayout(platform_layout)

        # Search Section
        search_layout = QHBoxLayout()
        self.plugin_search = QLineEdit()
        self.plugin_search.setPlaceholderText("Search plugins...")
        btn_search = QPushButton("Search")
        btn_search.clicked.connect(self.safe_plugin_search)
        search_layout.addWidget(self.plugin_search)
        search_layout.addWidget(btn_search)
        layout.addLayout(search_layout)

        # Results List
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.plugin_list_container = QWidget()
        self.plugin_list_layout = QVBoxLayout(self.plugin_list_container)
        scroll.setWidget(self.plugin_list_container)
        layout.addWidget(scroll)

    def setup_settings_tab(self, parent):
        layout = QFormLayout(parent)
        self.curseforge_key_input = QLineEdit()
        self.curseforge_key_input.setPlaceholderText("Enter CurseForge API key...")
        btn_save = QPushButton("Save API Key")
        btn_save.clicked.connect(self.save_api_keys)
        layout.addRow("CurseForge API Key:", self.curseforge_key_input)
        layout.addRow(btn_save)

    def create_url_section(self, target_type):
        url_layout = QVBoxLayout()
        url_layout.setContentsMargins(0, 2, 0, 2)
        url_layout.setSpacing(3)
        
        url_input = QTextEdit()
        url_input.setPlaceholderText(f"Paste {target_type} URLs (one per line)...")
        url_input.setMaximumHeight(60)
        url_input.setStyleSheet("padding: 2px;")
        
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
#endregion

#region Core Functionality
    def check_java(self):
        """Check system for Java installation and populate versions"""
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
            else:
                self.show_error(f"Unsupported Java version: {detected_version}")
                self.java_combo.setCurrentIndex(0)
                
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.show_error("Java runtime not found! Install Java 8+ first.")
            self.java_combo.setEnabled(False)

    def refresh_file_view(self):
        if self.current_server:
            self.file_view.setRootIndex(self.file_model.index(
                self.servers[self.current_server]['path']
            ))

    def validate_server_name(self, name):
        if not name.strip():
            raise ValueError("Server name cannot be empty!")
        if re.search(r'[<>:"/\\|?*]', name):
            raise ValueError("Invalid characters in server name!")
        if len(name) > 32:
            raise ValueError("Server name too long (max 32 characters)")
        if name.lower() in [n.lower() for n in self.servers]:
            raise ValueError("Server name already exists (case-insensitive)")

    def load_profiles(self):
        try:
            if os.path.exists(Constants.FILES["PROFILES"]):
                with open(Constants.FILES["PROFILES"], 'r') as f:
                    self.servers = json.load(f)
                    for server in self.servers.values():
                        server['thread'] = None
                    self.update_server_list()
        except Exception as e:
            self.show_error(f"Failed to load profiles: {str(e)}")

    def save_profiles(self):
        try:
            with open(Constants.FILES["PROFILES"], 'w') as f:
                save_data = {name: {k:v for k,v in data.items() if k != 'thread'} 
                           for name, data in self.servers.items()}
                json.dump(save_data, f, indent=2)
        except Exception as e:
            self.show_error(f"Failed to save profiles: {str(e)}")

    def check_api_keys(self):
        try:
            if os.path.exists(Constants.FILES["SETTINGS"]):
                with open(Constants.FILES["SETTINGS"], 'r') as f:
                    encrypted = f.read()
                    self.api_keys = json.loads(self.secure_settings.decrypt(encrypted))
                    self.curseforge_key_input.setText(self.api_keys.get('curseforge', ''))
        except Exception as e:
            logging.error(f"Secure load failed: {str(e)}")
    
    def save_api_keys(self):
        """Save API keys securely using encryption"""
        try:
            self.api_keys['curseforge'] = self.curseforge_key_input.text()
            encrypted = self.secure_settings.encrypt(json.dumps(self.api_keys))
            with open(Constants.FILES["SETTINGS"], 'w') as f:
                f.write(encrypted)
            self.statusBar().showMessage("API keys saved", 3000)
        except Exception as e:
            logging.error(f"API key save failed: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to save API keys")

#region Server Operations
    def select_server_directory(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select Directory", QDir.homePath(), QFileDialog.ShowDirsOnly
        )
        if path:
            self.server_path.setText(path)

    def create_new_server(self):
        if not self.server_path.text():
            self.show_error("Select server directory first!")
            return
        
        name, ok = QInputDialog.getText(self, "Server Name", "Enter server name:")
        if ok and name:
            try:
                self.validate_server_name(name)
                self.setup_server(name)
            except ValueError as e:
                self.show_error(str(e))

    def setup_server(self, name):
        server_dir = os.path.join(self.server_path.text(), name)
        try:
            os.makedirs(server_dir, exist_ok=True)
            loader = self.loader_combo.currentText()
            version = self.version_combo.currentText()
            
            self.servers[name] = {
                "path": server_dir,
                "status": Constants.SERVER_STATUS["STOPPED"],
                "max_ram": "2G",
                "mc_version": version,
                "thread": None
            }
            
            # Download server jar
            jar_path = os.path.join(server_dir, Constants.FILES["SERVER_JAR"])
            if loader == "Vanilla":
                self.download_file(self.get_vanilla_url(version), jar_path)
            elif loader == "Paper":
                self.download_file(self.get_paper_url(version), jar_path)
            else:
                raise ValueError("Unsupported loader")
            
            self.create_server_properties(server_dir)
            self.create_eula_file(server_dir)
            self.save_profiles()
            self.update_server_list()
            self.show_info(f"Server '{name}' created!")
        except Exception as e:
            self.show_error(f"Creation failed: {str(e)}")
            self.cleanup_server_dir(server_dir)

    def create_eula_file(self, path):
        with open(os.path.join(path, Constants.FILES["EULA_FILE"]), 'w') as f:
            f.write("eula=true\n")

    def update_versions(self, loader_name):
        self.version_combo.clear()
        self.progress.show()
        self.statusBar().showMessage("Fetching versions...")

        try:
            if loader_name == "Vanilla":
                response = requests.get(Constants.API_ENDPOINTS["VANILLA_MANIFEST"], timeout=10)
                versions = [v['id'] for v in response.json()['versions'] if v['type'] == 'release']
            elif loader_name == "Paper":
                # Get available Paper versions from API
                response = requests.get(Constants.API_ENDPOINTS["PAPER_VERSIONS"], timeout=10)
                paper_data = response.json()

                if 'versions' not in paper_data:
                    raise ValueError("Invalid PaperMC API response")

                # Filter valid versions (e.g., "1.20.4" but not "1.20.4-R0.1-SNAPSHOT")
                valid_versions = [
                    v for v in reversed(paper_data['versions'])
                    if re.match(r'^\d+\.\d+\.\d+$', v)  # Only X.X.X format
                ]

                if not valid_versions:
                    raise ValueError("No stable Paper versions available")

                versions = valid_versions
            else:
                versions = ["Version selection not implemented"]

            self.version_combo.addItems(versions)
            self.progress.hide()
            self.statusBar().showMessage(f"Loaded {len(versions)} versions", 3000)

        except Exception as e:
            self.progress.hide()
            self.show_error(f"Version fetch failed: {str(e)}")
            self.statusBar().showMessage("Version fetch failed", 3000)

    def get_vanilla_url(self, version):
        manifest = requests.get(Constants.API_ENDPOINTS["VANILLA_MANIFEST"]).json()
        for v in manifest['versions']:
            if v['id'] == version and v['type'] == "release":
                version_data = requests.get(v['url']).json()
                return version_data['downloads']['server']['url']
        raise ValueError("Version not found")

    def get_paper_url(self, version):
        """Get PaperMC download URL with debug logging"""
        try:
            # ------------------------------------------------------------------
            # Step 1: Get build list
            # ------------------------------------------------------------------
            builds_url = f"{Constants.API_ENDPOINTS['PAPER_VERSIONS']}/versions/{version}/builds"
            print(f"[DEBUG] Fetching builds from: {builds_url}")
            
            response = requests.get(builds_url, timeout=10)
            response.raise_for_status()
            builds_data = response.json()
            
            print(f"[DEBUG] Received {len(builds_data.get('builds', []))} builds for version {version}")
    
            # ------------------------------------------------------------------
            # Step 2: Validate builds
            # ------------------------------------------------------------------
            if not builds_data.get('builds'):
                print(f"[ERROR] No builds found in response for version {version}")
                raise ValueError(f"No builds available for PaperMC {version}")
    
            # ------------------------------------------------------------------
            # Step 3: Extract build details
            # ------------------------------------------------------------------
            latest_build = builds_data['builds'][-1]
            print(f"[DEBUG] Latest build details: {json.dumps(latest_build, indent=2)}")
    
            build_number = latest_build['build']
            downloads_data = latest_build['downloads']['application']
            filename = downloads_data['name']
            
            print(f"[DEBUG] Extracted values:")
            print(f"  - Version:    {version}")
            print(f"  - Build:      {build_number}")
            print(f"  - File name:  {filename}")
    
            # ------------------------------------------------------------------
            # Step 4: Construct final URL
            # ------------------------------------------------------------------
            download_url = (
                f"{Constants.API_ENDPOINTS['PAPER_VERSIONS']}/"
                f"versions/{version}/"
                f"builds/{build_number}/"
                f"downloads/{filename}"
            )
            print(f"[DEBUG] Constructed download URL: {download_url}")
            
            return download_url
    
        except requests.exceptions.HTTPError as e:
            print(f"[HTTP ERROR] Status: {e.response.status_code}")
            print(f"[HTTP ERROR] URL: {e.response.url}")
            if e.response.status_code == 404:
                raise ValueError(f"PaperMC version {version} not found")
            raise ValueError(f"API request failed: {str(e)}")
        except KeyError as e:
            print(f"[KEY ERROR] Missing field in API response: {str(e)}")
            print(f"[KEY ERROR] Response data: {json.dumps(builds_data, indent=2)}")
            raise ValueError(f"Missing required field in API response: {str(e)}")
        except json.JSONDecodeError:
            print(f"[JSON ERROR] Invalid response from: {builds_url}")
            print(f"[JSON ERROR] Response text: {response.text[:200]}...")
            raise ValueError("Invalid JSON response from PaperMC API")

    def update_server_list(self):
        self.server_list.clear()
        for server_name in self.servers:
            item = QListWidgetItem(server_name)
            status = self.servers[server_name].get('status', Constants.SERVER_STATUS["STOPPED"])
            item.setForeground(Qt.green if status == Constants.SERVER_STATUS["RUNNING"] else Qt.red)
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
            status = self.servers[self.current_server].get('status', Constants.SERVER_STATUS["STOPPED"])
            self.btn_start.setEnabled(status == Constants.SERVER_STATUS["STOPPED"])
            self.btn_stop.setEnabled(status == Constants.SERVER_STATUS["RUNNING"])

    def send_command(self):
        cmd = self.command_input.text()
        self.command_input.clear()
        if not self.current_server or not cmd:
            return
        
        try:
            server = self.servers[self.current_server]
            props_path = os.path.join(server['path'], Constants.FILES["SERVER_PROPERTIES"])
            
            with open(props_path, 'r') as f:
                config = {line.split('=')[0]: line.split('=')[1].strip() 
                        for line in f if '=' in line}
            
            with MCRcon("localhost", config['rcon.password'], int(config['rcon.port']), 
                      timeout=Constants.RCON_TIMEOUT) as mcr:
                response = mcr.command(cmd)
                self.console_output.append(f"> {cmd}\n{response}")
        except ConnectionRefusedError:
            self.show_error("RCON connection refused - check if enabled")
        except Exception as e:
            self.show_error(f"Command failed: {str(e)}")

    def start_server(self):
        if not self.current_server:
            return
        
        server = self.servers[self.current_server]
        if server['status'] == Constants.SERVER_STATUS["STOPPED"]:
            try:
                java_path = "java.exe" if platform.system() == "Windows" else "java"
                command = [
                    java_path,
                    f"-Xmx{server.get('max_ram', '2G')}",
                    "-jar",
                    Constants.FILES["SERVER_JAR"],
                    "nogui"
                ]
                
                server['thread'] = ServerThread(command, server['path'])
                server['thread'].output.connect(self.handle_server_output)
                server['thread'].stopped.connect(self.handle_server_stop)
                server['thread'].start()
                server['status'] = Constants.SERVER_STATUS["RUNNING"]
                self.save_profiles()
                self.update_server_list()
                self.statusBar().showMessage("Server starting...", 3000)
            except Exception as e:
                self.show_error(f"Start failed: {str(e)}")

    def handle_server_output(self, message):
        try:
            self.console_output.append(message)
            # Error detection
            if any(e in message for e in ["ERROR", "Exception", "Crash"]):
                logging.error(f"Server Error: {message}")
                self.show_error_notification(message)
            
            # Line limit management
            if self.console_output.document().lineCount() > Constants.MAX_CONSOLE_LINES:
                cursor = self.console_output.textCursor()
                cursor.movePosition(cursor.Start)
                cursor.select(cursor.LineUnderCursor)
                cursor.removeSelectedText()
        except Exception as e:
            logging.error(f"Output handling error: {str(e)}")

    def handle_server_stop(self):
        if self.current_server:
            server = self.servers[self.current_server]
            server['status'] = Constants.SERVER_STATUS["STOPPED"]
            server['thread'] = None
            self.save_profiles()
            self.update_server_list()
            self.statusBar().showMessage("Server stopped", 3000)

    def stop_server(self):
        if self.current_server and self.servers[self.current_server]['status'] == Constants.SERVER_STATUS["RUNNING"]:
            try:
                self.send_command("stop")
                self.servers[self.current_server]['thread'].stop()
                self.servers[self.current_server]['status'] = Constants.SERVER_STATUS["STOPPING"]
                self.save_profiles()
                self.update_server_list()
                self.statusBar().showMessage("Stopping server...", 3000)
            except Exception as e:
                self.show_error(f"Stop failed: {str(e)}")

    def delete_server(self):
        if not self.current_server:
            return
        
        reply = QMessageBox.question(
            self, "Delete Server", f"Permanently delete '{self.current_server}'?",
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
                self.show_error(f"Delete failed: {str(e)}")

    def create_backup(self):
        if not self.current_server:
            return
            
        try:
            server_path = self.servers[self.current_server]['path']
            backup_path = BackupManager(server_path).create_backup()
            self.show_info(f"Backup created: {os.path.basename(backup_path)}")
        except Exception as e:
            self.show_error(f"Backup failed: {str(e)}")

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
                self.show_error(f"Open failed: {str(e)}")
#endregion

#region Mod/Plugin Management
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
                self.search_thread.finished.connect(lambda data: self.show_mods(self._format_modrinth_results(data)))
            elif platform == "CurseForge":
                if not self.api_keys.get('curseforge'):
                    self.show_error("CurseForge API key required!")
                    return
                self.search_thread = CurseForgeSearchThread(query, 6, self.api_keys['curseforge'])
                self.search_thread.finished.connect(lambda data: self.show_mods(self._format_curseforge_results(data)))
            
            self.search_thread.error.connect(self.show_search_error)
            self.search_thread.start()
        except Exception as e:
            self.progress.hide()
            self.show_error(f"Search failed: {str(e)}")

    def safe_plugin_search(self):
        platform = self.plugins_platform_combo.currentText()
        query = self.plugin_search.text()
        
        if not query:
            return
            
        self.progress.show()
        self.statusBar().showMessage("Searching plugins...")
        
        try:
            if platform == "CurseForge":
                if not self.api_keys.get('curseforge'):
                    self.show_error("CurseForge API key required!")
                    return
                self.search_thread = CurseForgeSearchThread(query, 5, self.api_keys['curseforge'])
                self.search_thread.finished.connect(lambda data: self.show_plugins(self._format_curseforge_results(data)))
            elif platform == "Modrinth":
                server_version = self.servers[self.current_server]['mc_version'] if self.current_server else None
                self.search_thread = ModSearchThread(query, server_version, "bukkit")
                self.search_thread.finished.connect(lambda data: self.show_plugins(self._format_modrinth_results(data)))
            
            self.search_thread.error.connect(self.show_search_error)
            self.search_thread.start()
        except Exception as e:
            self.progress.hide()
            self.show_error(f"Search failed: {str(e)}")

    def _format_modrinth_results(self, results):
        return [{
            'id': res['project_id'],
            'title': res['title'],
            'description': res.get('description', 'No description'),
            'icon_url': res.get('icon_url'),
            'versions': res['versions']
        } for res in results]

    def _format_curseforge_results(self, results):
        return [{
            'id': res['id'],
            'name': res['name'],
            'description': res.get('summary', 'No description'),
            'icon_url': res['logo']['url'] if res.get('logo') else None,
            'versions': res['latestFiles']
        } for res in results]

    def show_mods(self, mods):
        self.clear_layout(self.mod_list_layout)
        for mod in mods:
            self.create_resource_card(mod, self.mod_list_layout, self.install_mod)
        self.mod_list_layout.addStretch()
        self.statusBar().showMessage(f"Found {len(mods)} mods", 3000)

    def show_plugins(self, plugins):
        self.clear_layout(self.plugin_list_layout)
        for plugin in plugins:
            self.create_resource_card(plugin, self.plugin_list_layout, self.install_plugin)
        self.plugin_list_layout.addStretch()
        self.statusBar().show_message(f"Found {len(plugins)} plugins", 3000)

    def create_resource_card(self, data, layout, install_handler):
        widget = QWidget()
        widget.setFixedHeight(100)
        
        hbox = QHBoxLayout(widget)
        icon = QLabel()
        icon.setFixedSize(80, 80)
        
        text = QVBoxLayout()
        title = QLabel(f"<b>{data.get('title', data.get('name'))}</b>")
        desc = QLabel(data.get('description', 'No description'))
        text.addWidget(title)
        text.addWidget(desc)
        
        btn = QPushButton("Install")
        btn.clicked.connect(lambda _, d=data: install_handler(d))
        
        hbox.addWidget(icon)
        hbox.addLayout(text)
        hbox.addWidget(btn)
        layout.addWidget(widget)
        
        if data.get('icon_url'):
            self.load_item_icon(data['id'], data['icon_url'], icon)

    def load_item_icon(self, item_id, url, target_label):
        loader = ImageLoaderThread(url, item_id)
        loader.loaded.connect(lambda i, p: self.update_icon(target_label, p))
        loader.finished.connect(lambda: self.current_image_loaders.remove(loader))
        self.current_image_loaders.append(loader)
        loader.start()

    def update_icon(self, label, pixmap):
        label.setPixmap(pixmap.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def install_mod(self, mod):
        self.install_resource(mod, "mods")

    def install_plugin(self, plugin):
        self.install_resource(plugin, "plugins")

    def install_resource(self, resource, target_type):
        if not self.current_server:
            self.show_error("Select a server first!")
            return
        
        try:
            server_path = self.servers[self.current_server]['path']
            target_dir = os.path.join(server_path, target_type)
            os.makedirs(target_dir, exist_ok=True)
            
            if self.mods_platform_combo.currentText() == "Modrinth":
                version = resource['versions'][0]
                file = version['files'][0]
                url = file['url']
                filename = file['filename']
            else:
                file = resource['versions'][0]
                url = file['downloadUrl']
                filename = file['fileName']
            
            dest_path = os.path.join(target_dir, filename)
            if os.path.exists(dest_path):
                reply = QMessageBox.question(
                    self, "File Exists", 
                    f"{filename} already exists. Overwrite?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    return
            
            self.download_file(url, dest_path)
            self.show_info(f"Installed {filename}")
        except Exception as e:
            self.show_error(f"Install failed: {str(e)}")

    def clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()
#endregion

#region Utility Methods
    def show_info(self, message):
        QMessageBox.information(self, "Success", message)
        self.statusBar().showMessage(message, 3000)

    def show_error(self, message):
        QMessageBox.critical(self, "Error", message)
        self.statusBar().showMessage(f"Error: {message}", 5000)

    def show_search_error(self, message):
        self.progress.hide()
        self.show_error(f"Search error: {message}")

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
            self.show_error(f"Download failed: {str(e)}")
            raise

    def cleanup_server_dir(self, path):
        try:
            if os.path.exists(path):
                if platform.system() == "Windows":
                    subprocess.run(['cmd', '/c', 'rmdir', '/s', '/q', path], check=True)
                else:
                    subprocess.run(["rm", "-rf", path], check=True)
        except Exception as e:
            self.show_error(f"Cleanup failed: {str(e)}")

    def setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self.refresh_file_view)
        QShortcut(QKeySequence("Ctrl+Q"), self).activated.connect(self.close)

    def closeEvent(self, event):
        for loader in self.current_image_loaders:
            if loader.isRunning():
                loader.quit()
        self.save_profiles()
        self.save_api_keys()
        event.accept()
#endregion

if __name__ == "__main__":
    app = QApplication(sys.argv)
    qdarktheme.setup_theme()
    window = ServerManager()
    window.show()
    sys.exit(app.exec())