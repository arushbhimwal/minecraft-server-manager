#!/usr/bin/env python3
import sys
import os
import json
import time
import random
import requests
import shutil
import tempfile
import platform
import subprocess
import logging
import re
import zipfile
import secrets
import stat
import hashlib
from datetime import datetime
from functools import lru_cache
from collections import deque
from cryptography.fernet import Fernet
from mcrcon import MCRcon
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QTabWidget, QTextEdit,
    QLineEdit, QFileDialog, QMessageBox, QTreeView, QFileSystemModel,
    QInputDialog, QListWidget, QListWidgetItem, QProgressBar,
    QScrollArea, QFormLayout
)
from PySide6.QtGui import QPixmap, QImage, QColor, QKeySequence, QShortcut
from PySide6.QtCore import Qt, QThread, Signal, QDir, QRunnable, QThreadPool
import qdarktheme

# Constants
class Constants:
    HEADERS = {
        "DEFAULT_USER_AGENT": "MinecraftServerManager/1.0 (+https://github.com/arushbhimwal/minecraft-server-manager)"
    }
    SERVER_STATUS = {
        "STOPPED": "stopped", 
        "RUNNING": "running", 
        "STARTING": "starting"
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

class SecureSettings:
    def __init__(self):
        self.cipher = self._get_cipher()
        
    def _get_cipher(self):
        if not os.path.exists(Constants.SECURITY_KEY_FILE):
            key = Fernet.generate_key()
            with open(Constants.SECURITY_KEY_FILE, 'wb') as f: 
                f.write(key)
            # Set restrictive permissions (owner read/write only)
            if platform.system() != 'Windows':
                os.chmod(Constants.SECURITY_KEY_FILE, 0o600)
        else:
            with open(Constants.SECURITY_KEY_FILE, 'rb') as f: 
                key = f.read()
        return Fernet(key)
    
    def encrypt(self, data):
        return self.cipher.encrypt(data.encode()).decode()
    
    def decrypt(self, encrypted_data):
        return self.cipher.decrypt(encrypted_data.encode()).decode()
    
class FileDownloader:
    """Robust file downloader with improved output parsing"""
    
    def __init__(self):
        self.max_retries = 5
        self.retry_delay_base = 1  # seconds
        self.curl_timeout = 600  # 10 minutes timeout
    
    def download_file(self, url, path):
        """Download directly to target path"""
        # Create target directory if needed
        target_dir = os.path.dirname(path)
        os.makedirs(target_dir, exist_ok=True)
        
        # Use curl if available, otherwise fallback to requests
        if shutil.which("curl"):
            return self._download_with_curl(url, path)
        else:
            return self._download_with_requests(url, path)
    
    def _download_with_curl(self, url, path):
        curl_cmd = [
            "curl",
            "-L",  # Follow redirects
            "-o", path,  # Download directly to target path
            "-w", "%{http_code} %{size_download}",  # Only require these two values
            "-s",  # Silent mode
            "-S",  # Show errors even in silent mode
            "--max-time", str(self.curl_timeout),
            "--retry", "3",  # Retry on transient errors
            "--retry-delay", "2",  # Wait between retries
            "--fail",  # Fail on HTTP errors
            url
        ]
        
        for attempt in range(self.max_retries):
            try:
                # Run curl command
                result = subprocess.run(
                    curl_cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=self.curl_timeout + 30
                )
                
                # Parse curl output
                output = result.stdout.strip()
                if not output:
                    raise ValueError("Empty response from curl")
                
                # Split output into parts
                parts = output.split()
                
                # Validate we have at least 2 parts (http_code and downloaded size)
                if len(parts) < 2:
                    raise ValueError(
                        f"Unexpected curl output format. "
                        f"Expected at least 2 values, got {len(parts)}: {output}"
                    )
                
                http_code = parts[0]
                downloaded_size = int(parts[1])
                
                # Validate HTTP status
                if not http_code.isdigit() or int(http_code) >= 400:
                    raise ValueError(f"HTTP error: {http_code}")
                
                # Verify file exists
                if not os.path.exists(path):
                    raise FileNotFoundError(f"File not created at {path}")
                
                # Get actual file size
                actual_size = os.path.getsize(path)
                
                # Compare curl's reported size with actual file size
                if actual_size != downloaded_size:
                    raise ValueError(
                        f"File size mismatch: curl reported {downloaded_size}, "
                        f"actual size {actual_size}"
                    )
                
                return path
                
            except (subprocess.CalledProcessError, ValueError, OSError) as e:
                error_msg = f"Download error (curl): {str(e)}"
                
                # Clean up partial download
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay_base * (2 ** attempt)
                    time.sleep(delay)
                else:
                    # Fallback to requests after curl failures
                    return self._download_with_requests(url, path)
    
    def _download_with_requests(self, url, path):
        for attempt in range(self.max_retries):
            try:
                with requests.get(url, stream=True, timeout=30) as r:
                    r.raise_for_status()
                    
                    # Create temporary file with secure permissions
                    with tempfile.NamedTemporaryFile(delete=False) as tmp:
                        # Download content
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:  # filter out keep-alive chunks
                                tmp.write(chunk)
                        
                        # Set secure permissions
                        if platform.system() != 'Windows':
                            os.chmod(tmp.name, 0o600)
                        
                        # Move to final location
                        os.replace(tmp.name, path)
                    
                    # Verify file
                    if not os.path.exists(path):
                        raise FileNotFoundError(f"File not created at {path}")
                    
                    return path
                    
            except Exception as e:
                error_msg = f"Download error (requests): {str(e)}"
                
                # Clean up partial download
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay_base * (2 ** attempt)
                    time.sleep(delay)
                else:
                    raise RuntimeError(f"Download failed after {self.max_retries} attempts: {error_msg}")

class BackupManager:
    """Handles server backups"""
    
    def __init__(self, server_path):
        self.server_path = server_path
        
    def create_backup(self):
        """Create a zip backup of the server"""
        # Let user choose backup location
        backup_dir = QFileDialog.getExistingDirectory(
            None, "Select Backup Location", 
            os.path.expanduser("~"),
            QFileDialog.ShowDirsOnly
        )
        
        if not backup_dir:
            return None
            
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = os.path.join(backup_dir, f"backup-{timestamp}.zip")
        
        try:
            with zipfile.ZipFile(backup_path, 'w') as zipf:
                for root, _, files in os.walk(self.server_path):
                    # Skip backup directory itself
                    if Constants.BACKUP_DIR in root:
                        continue
                        
                    for file in files:
                        full_path = os.path.join(root, file)
                        arcname = os.path.relpath(full_path, self.server_path)
                        zipf.write(full_path, arcname)
            return backup_path
        except Exception as e:
            logging.error(f"Backup failed: {str(e)}")
            raise

class ModrinthAPI:
    @lru_cache(maxsize=100)
    def get_versions(self, project_id):
        headers = {'User-Agent': Constants.HEADERS["DEFAULT_USER_AGENT"]}
        response = requests.get(
            f'https://api.modrinth.com/v2/project/{project_id}/version',
            headers=headers
        )
        return response.json()

class CurseForgeAPI:
    @lru_cache(maxsize=100)
    def get_file_info(self, file_id, api_key):
        headers = {'x-api-key': api_key}
        response = requests.get(
            f'https://api.curseforge.com/v1/mods/files/{file_id}',
            headers=headers
        )
        return response.json()

class ImageLoader(QRunnable):
    """Runnable for loading item icons"""
    loaded = Signal(str, QPixmap)  # (item_id, pixmap)

    def __init__(self, url, item_id):
        super().__init__()
        self.url = url
        self.item_id = item_id
        self.is_cancelled = False

    def run(self):
        if self.is_cancelled:
            return
            
        try:
            response = requests.get(self.url, timeout=10)
            img = QImage.fromData(response.content)
            pixmap = QPixmap.fromImage(img)
            self.loaded.emit(self.item_id, pixmap)
        except Exception:
            # Create a placeholder pixmap
            pixmap = QPixmap(80, 80)
            pixmap.fill(QColor(200, 200, 200))
            self.loaded.emit(self.item_id, pixmap)

class ModSearchThread(QThread):
    """Thread for searching mods on Modrinth"""
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, query, mc_version, loader):
        super().__init__()
        self.query = query
        self.mc_version = mc_version
        self.loader = loader
        self.api = ModrinthAPI()

    def run(self):
        try:
            # Construct facets for search
            facets = []
            if self.mc_version:
                facets.append(f"versions:{self.mc_version}")
            if self.loader:
                facets.append(f"categories:{self.loader.lower()}")
            
            params = {
                'query': self.query,
                'facets': json.dumps([facets]) if facets else '',
                'limit': 10
            }
            
            response = requests.get(
                'https://api.modrinth.com/v2/search',
                params=params,
                headers={'User-Agent': Constants.HEADERS["DEFAULT_USER_AGENT"]},
                timeout=10
            )
            response.raise_for_status()
            
            results = []
            for hit in response.json()['hits']:
                versions = self.api.get_versions(hit['project_id'])
                results.append({
                    'project_id': hit['project_id'],
                    'title': hit['title'],
                    'description': hit['description'],
                    'icon_url': hit.get('icon_url'),
                    'versions': versions
                })
                
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))

class CurseForgeSearchThread(QThread):
    """Thread for searching mods on CurseForge"""
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, query, category_id, api_key):
        super().__init__()
        self.query = query
        self.category_id = category_id
        self.api_key = api_key

    def run(self):
        try:
            headers = {'x-api-key': self.api_key}
            params = {
                'gameId': 432,  # Minecraft
                'categoryId': self.category_id,
                'searchFilter': self.query,
                'pageSize': 10
            }
            
            response = requests.get(
                'https://api.curseforge.com/v1/mods/search',
                params=params,
                headers=headers,
                timeout=10
            )
            response.raise_for_status()
            
            results = []
            for mod in response.json()['data']:
                results.append({
                    'id': mod['id'],
                    'name': mod['name'],
                    'summary': mod.get('summary', ''),
                    'logo': mod.get('logo', {}),
                    'latestFiles': mod.get('latestFiles', [])
                })
                
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))

class UrlInstallThread(QThread):
    """Thread for installing mods/plugins from URLs"""
    progress = Signal(int, str)  # (progress_value, status_message)
    finished = Signal()
    error = Signal(str)

    def __init__(self, urls, server_path, api_key, target_type):
        super().__init__()
        self.urls = urls
        self.server_path = server_path
        self.api_key = api_key
        self.target_type = target_type  # "mods" or "plugins"
        self.downloader = FileDownloader()
        self.url_regex = re.compile(
            r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[^\s]*'
        )

    def run(self):
        try:
            # Validate URLs
            valid_urls = []
            for url in self.urls:
                if not url.strip():
                    continue
                if self.url_regex.match(url):
                    valid_urls.append(url)
                else:
                    self.error.emit(f"Invalid URL skipped: {url}")
            
            total = len(valid_urls)
            if total == 0:
                self.error.emit("No valid URLs found")
                return
                
            success = 0
            target_dir = os.path.join(self.server_path, self.target_type)
            
            # Create directory if needed
            os.makedirs(target_dir, exist_ok=True)
            
            for i, url in enumerate(valid_urls):
                self.progress.emit(int(100 * i / total), f"Processing URL {i+1}/{total}")
                
                try:
                    if "curseforge.com" in url:
                        filename = self.process_curseforge_url(url, target_dir)
                    else:
                        filename = self.download_direct(url, target_dir)
                    
                    success += 1
                    self.progress.emit(int(100 * (i+1) / total), 
                                      f"Installed: {filename}")
                except Exception as e:
                    self.error.emit(f"URL {url} failed: {str(e)}")
            
            self.progress.emit(100, f"Successfully installed {success}/{total} items")
            self.finished.emit()
        except Exception as e:
            self.error.emit(f"Installation failed: {str(e)}")

    def process_curseforge_url(self, url, target_dir):
        """Process CurseForge URLs using their API"""
        # Extract project ID from URL
        match = re.search(r'/projects/([^/]+)', url)
        if not match:
            raise ValueError("Invalid CurseForge URL format")
        
        project_slug = match.group(1)
        headers = {'x-api-key': self.api_key}
        
        # Get project ID
        search_url = f"https://api.curseforge.com/v1/mods/search?gameId=432&slug={project_slug}"
        response = requests.get(search_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        if not data['data']:
            raise ValueError("Project not found")
            
        project_id = data['data'][0]['id']
        
        # Get latest file
        files_url = f"https://api.curseforge.com/v1/mods/{project_id}/files"
        response = requests.get(files_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        files_data = response.json()
        if not files_data['data']:
            raise ValueError("No files available for this project")
            
        file_info = files_data['data'][0]
        download_url = file_info['downloadUrl']
        filename = file_info['fileName']
        
        if not download_url:
            raise ValueError("No download URL available")
            
        dest_path = os.path.join(target_dir, filename)
        self.downloader.download_file(download_url, dest_path)
        return filename

    def download_direct(self, url, target_dir):
        """Download directly from URL"""
        # Extract filename from URL
        parsed = requests.utils.urlparse(url)
        filename = os.path.basename(parsed.path)
        
        if not filename:
            # Generate filename if not found in URL
            filename = f"downloaded_{int(time.time())}.jar"
        
        dest_path = os.path.join(target_dir, filename)
        self.downloader.download_file(url, dest_path)
        return filename
    
class ServerThread(QThread):
    """Thread for running the Minecraft server"""
    output = Signal(str)
    stopped = Signal()
    error = Signal(str)

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
            self.error.emit(f"Server thread error: {str(e)}")
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

class ServerManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.java_versions = ['8', '11', '17', '21']
        self.loaders = ['Vanilla', 'Paper', 'Spigot', 'Forge', 'Fabric', 'Quilt', 'Mohist']
        self.servers = {}
        self.current_server = None
        self.api_keys = {'curseforge': ''}
        self.secure_settings = SecureSettings()
        self.console_buffer = deque(maxlen=Constants.MAX_CONSOLE_LINES)
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(4)
        
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
        # Changed from send_command to on_command_enter
        self.command_input.returnPressed.connect(self.on_command_enter)
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

    def setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self.refresh_file_view)
        QShortcut(QKeySequence("Ctrl+Q"), self).activated.connect(self.close)

    def show_info(self, message):
        QMessageBox.information(self, "Success", message)
        self.statusBar().showMessage(message, 3000)

    def show_error(self, message):
        QMessageBox.critical(self, "Error", message)
        self.statusBar().showMessage(f"Error: {message}", 5000)

    def show_search_error(self, message):
        self.progress.hide()
        self.show_error(f"Search error: {message}")

    def cleanup_server_dir(self, path):
        try:
            if os.path.exists(path):
                if platform.system() == "Windows":
                    subprocess.run(['cmd', '/c', 'rmdir', '/s', '/q', path], check=True)
                else:
                    subprocess.run(["rm", "-rf", path], check=True)
        except Exception as e:
            self.show_error(f"Cleanup failed: {str(e)}")

    def closeEvent(self, event):
        self.thread_pool.waitForDone(3000)
        self.save_profiles()
        self.save_api_keys()
        event.accept()

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

            jar_path = os.path.join(server_dir, Constants.FILES["SERVER_JAR"])

            # Use the robust downloader
            downloader = FileDownloader()

            if loader == "Vanilla":
                downloader.download_file(self.get_vanilla_url(version), jar_path)
            elif loader == "Paper":
                downloader.download_file(self.get_paper_url(version), jar_path)
            else:
                raise ValueError("Unsupported loader")

            # Create server configuration files
            self.create_server_properties(server_dir)
            self.create_eula_file(server_dir)

            # Generate secure RCON password
            rcon_password = secrets.token_urlsafe(16)
            
            # Save server profile
            self.servers[name] = {
                "path": server_dir,
                "status": Constants.SERVER_STATUS["STOPPED"],
                "max_ram": "2G",
                "mc_version": version,
                "thread": None,
                "rcon_password": self.secure_settings.encrypt(rcon_password)
            }
            self.save_profiles()
            self.update_server_list()
            self.show_info(f"Server '{name}' created!")
        except Exception as e:
            self.show_error(f"Server creation failed: {str(e)}")
            # Cleanup partial server directory
            if os.path.exists(server_dir):
                shutil.rmtree(server_dir, ignore_errors=True)

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
                server['thread'].error.connect(self.handle_thread_error)
                server['thread'].start()
                server['status'] = Constants.SERVER_STATUS["RUNNING"]
                self.save_profiles()
                self.update_server_list()
                self.statusBar().showMessage("Server starting...", 3000)
            except Exception as e:
                self.show_error(f"Start failed: {str(e)}")

    def handle_server_output(self, message):
        try:
            # Add to circular buffer
            self.console_buffer.append(message)
            # Update console with full buffer content
            self.console_output.setText("\n".join(self.console_buffer))
            
            # Error detection
            if any(e in message for e in ["ERROR", "Exception", "Crash"]):
                logging.error(f"Server Error: {message}")
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

    def handle_thread_error(self, error):
        self.show_error(f"Server error: {error}")

    def stop_server(self):
        if self.current_server and self.servers[self.current_server]['status'] == Constants.SERVER_STATUS["RUNNING"]:
            try:
                # Use send_rcon_command instead of send_command
                self.send_rcon_command("stop")
                self.servers[self.current_server]['thread'].stop()
                self.servers[self.current_server]['status'] = Constants.SERVER_STATUS["STOPPING"]
                self.save_profiles()
                self.update_server_list()
                self.statusBar().showMessage("Stopping server...", 3000)
            except Exception as e:
                self.show_error(f"Stop failed: {str(e)}")

    def send_rcon_command(self, cmd):
        """Send RCON command directly"""
        if not self.current_server or not cmd:
            return
        
        try:
            server = self.servers[self.current_server]
            props_path = os.path.join(server['path'], Constants.FILES["SERVER_PROPERTIES"])
            
            with open(props_path, 'r') as f:
                config = {line.split('=')[0]: line.split('=')[1].strip() 
                        for line in f if '=' in line}
            
            # Decrypt RCON password
            rcon_password = self.secure_settings.decrypt(server['rcon_password'])
            
            with MCRcon("localhost", rcon_password, int(config['rcon.port']), 
                      timeout=Constants.RCON_TIMEOUT) as mcr:
                response = mcr.command(cmd)
                self.console_output.append(f"> {cmd}\n{response}")
        except ConnectionRefusedError:
            self.show_error("RCON connection refused - check if enabled")
        except Exception as e:
            self.show_error(f"Command failed: {str(e)}")

    # This method stays the same - it handles console input
    def on_command_enter(self):
        cmd = self.command_input.text()
        self.command_input.clear()
        if not self.current_server or not cmd:
            return
        self.send_rcon_command(cmd)

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
                # Stop server if running
                server = self.servers[self.current_server]
                if server['status'] != Constants.SERVER_STATUS["STOPPED"]:
                    self.stop_server()
                    # Wait for server to stop
                    for _ in range(10):  # 10 attempts with 0.5s delay
                        if server['status'] == Constants.SERVER_STATUS["STOPPED"]:
                            break
                        time.sleep(0.5)

                # Use more robust deletion method
                if os.path.exists(server_path):
                    # Use shutil.rmtree which handles permissions better
                    def on_error(func, path, exc_info):
                        # Try to fix permissions and retry
                        os.chmod(path, stat.S_IWRITE)
                        func(path)

                    shutil.rmtree(server_path, onerror=on_error)

                    # Verify deletion
                    if os.path.exists(server_path):
                        raise RuntimeError(f"Failed to delete directory: {server_path}")

                del self.servers[self.current_server]
                self.current_server = None
                self.save_profiles()
                self.update_server_list()
                self.statusBar().showMessage("Server deleted", 3000)
            except Exception as e:
                self.show_error(f"Delete failed: {str(e)}\n\n"
                               "Common solutions:\n"
                               "1. Close any programs using the directory\n"
                               "2. Check file permissions\n"
                               "3. Delete manually: " + server_path)
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
            if backup_path:
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

    def create_server_properties(self, server_dir):
        """Create default server.properties file with secure RCON"""
        # Generate secure RCON password
        rcon_password = secrets.token_urlsafe(16)
        
        properties_path = os.path.join(server_dir, "server.properties")
        with open(properties_path, 'w') as f:
            f.write("# Minecraft server properties\n")
            f.write("enable-jmx-monitoring=false\n")
            f.write("rcon.port=25575\n")
            f.write("level-seed=\n")
            f.write("enable-command-block=false\n")
            f.write("gamemode=survival\n")
            f.write("enable-query=false\n")
            f.write("generator-settings={}\n")
            f.write("level-name=world\n")
            f.write("motd=A Minecraft Server\n")
            f.write("query.port=25565\n")
            f.write("pvp=true\n")
            f.write("generate-structures=true\n")
            f.write("difficulty=normal\n")
            f.write("network-compression-threshold=256\n")
            f.write("max-tick-time=60000\n")
            f.write("require-resource-pack=false\n")
            f.write("use-native-transport=true\n")
            f.write("max-players=20\n")
            f.write("online-mode=false\n")
            f.write("enable-status=true\n")
            f.write("allow-flight=false\n")
            f.write("broadcast-rcon-to-ops=true\n")
            f.write("view-distance=16\n")
            f.write("server-ip=\n")
            f.write("resource-pack-prompt=\n")
            f.write("allow-nether=true\n")
            f.write("server-port=25565\n")
            f.write("enable-rcon=true\n")  # Enable RCON by default
            f.write("sync-chunk-writes=true\n")
            f.write("op-permission-level=4\n")
            f.write("prevent-proxy-connections=false\n")
            f.write("hide-online-players=false\n")
            f.write("resource-pack=\n")
            f.write("entity-broadcast-range-percentage=100\n")
            f.write("simulation-distance=16\n")
            f.write(f"rcon.password={rcon_password}\n")  # Set secure password
            f.write("player-idle-timeout=0\n")
            f.write("debug=false\n")
            f.write("force-gamemode=true\n")
            f.write("rate-limit=0\n")
            f.write("hardcore=false\n")
            f.write("white-list=false\n")
            f.write("broadcast-console-to-ops=true\n")
            f.write("spawn-npcs=true\n")
            f.write("spawn-animals=true\n")
            f.write("snooper-enabled=true\n")
            f.write("function-permission-level=2\n")
            f.write("level-type=default\n")
            f.write("text-filtering-config=\n")
            f.write("spawn-monsters=true\n")
            f.write("enforce-whitelist=false\n")
            f.write("resource-pack-sha1=\n")
            f.write("spawn-protection=0\n")
            f.write("max-world-size=29999984\n")

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
        self.statusBar().showMessage(f"Found {len(plugins)} plugins", 3000)

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
        loader = ImageLoader(url, item_id)
        loader.loaded.connect(lambda i, p: self.update_icon(target_label, p))
        self.thread_pool.start(loader)

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
            mc_version = self.servers[self.current_server]['mc_version']
            
            if self.mods_platform_combo.currentText() == "Modrinth":
                # Filter compatible versions
                compatible_versions = [
                    v for v in resource['versions']
                    if mc_version in v.get('game_versions', [])
                ]
                if not compatible_versions:
                    raise ValueError("No compatible version found for your Minecraft version")
                    
                version = compatible_versions[0]
                file = version['files'][0]
                url = file['url']
                filename = file['filename']
            else:
                # For CurseForge, use first file for now
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
            
            # Use our robust downloader
            downloader = FileDownloader()
            downloader.download_file(url, dest_path)
            self.show_info(f"Installed {filename}")
        except Exception as e:
            self.show_error(f"Install failed: {str(e)}")

    def clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()

    def start_url_install(self, target_type):
        """Start URL installation process"""
        # Get the URL input for the target type
        url_input = getattr(self, f"{target_type}_url_input")
        urls = url_input.toPlainText().split('\n')
        
        if not self.current_server:
            self.show_error("Select a server first!")
            return
            
        server_path = self.servers[self.current_server]['path']
        api_key = self.api_keys.get('curseforge', '')
        
        # Get progress UI elements
        progress_bar = getattr(self, f"{target_type}_progress")
        status_label = getattr(self, f"{target_type}_status")
        
        # Create and configure thread
        self.install_thread = UrlInstallThread(urls, server_path, api_key, target_type)
        self.install_thread.progress.connect(
            lambda val, text: (progress_bar.setValue(val), status_label.setText(text))
        )
        self.install_thread.finished.connect(lambda: status_label.setText("Installation completed"))
        self.install_thread.error.connect(lambda err: status_label.setText(f"Error: {err}"))
        
        # Reset UI and start thread
        progress_bar.setValue(0)
        status_label.setText("Starting installation...")
        self.install_thread.start()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    qdarktheme.setup_theme()
    window = ServerManager()
    window.show()
    sys.exit(app.exec())