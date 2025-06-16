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
import logging.handlers  # Added missing import
import re
import zipfile
import secrets
import signal
import fnmatch
from datetime import datetime
from functools import lru_cache
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
import base64
from mcrcon import MCRcon

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QTabWidget, QTextEdit,
    QLineEdit, QFileDialog, QMessageBox, QTreeView, QFileSystemModel,
    QInputDialog, QListWidget, QListWidgetItem, QProgressBar,
    QScrollArea, QFormLayout, QDialog, QDialogButtonBox, QGridLayout
)
from PySide6.QtGui import QPixmap, QImage, QColor, QKeySequence, QShortcut, QFont, QStandardItemModel, QStandardItem
from PySide6.QtCore import Qt, QThread, Signal, QDir, QTimer, QProcess, QMutex
import qdarktheme

# =============================================================================
# CONSTANTS & SECURITY SETTINGS
# =============================================================================
class Constants:
    HEADERS = {
        "DEFAULT_USER_AGENT": "MinecraftServerManager/1.0 (+https://github.com/arushbhimwal/minecraft-server-manager)"
    }
    SERVER_STATUS = {
        "STOPPED": "stopped", 
        "RUNNING": "running", 
        "STARTING": "starting",
        "STOPPING": "stopping"
    }
    FILES = {
        "PROFILES": "profiles.json",
        "SETTINGS": "settings.json",
        "SERVER_JAR": "server.jar",
        "SERVER_PROPERTIES": "server.properties",
        "EULA_FILE": "eula.txt",
        "MOD_MANIFEST": "mod_manifest.json",
        "PID_FILE": "server.pid"
    }
    API_ENDPOINTS = {
        "VANILLA_MANIFEST": "https://piston-meta.mojang.com/mc/game/version_manifest.json",
        "PAPER_VERSIONS": "https://api.papermc.io/v2/projects/paper",
        "MODRINTH_VERSIONS": "https://api.modrinth.com/v2/tag/game_version",
        "FABRIC_VERSIONS": "https://meta.fabricmc.net/v2/versions/game",
        "MODRINTH_SEARCH": "https://api.modrinth.com/v2/search"
    }
    MAX_CONSOLE_LINES = 1000
    RCON_TIMEOUT = 5
    BACKUP_DIR = "backups"
    SECURITY_KEY_FILE = ".encryption.key"
    MODRINTH_PAGE_SIZE = 10
    MAX_CACHE_SIZE = 32
    JAVA_VERSIONS = ['8', '11', '17', '21']
    LOADERS = ['Vanilla', 'Paper', 'Spigot', 'Forge', 'Fabric', 'Quilt', 'Mohist']
    EXCLUDE_BACKUP_PATTERNS = [
        "session.lock", "*.tmp", "*.temp", "*.bak", 
        BACKUP_DIR + "/*", "logs/*", "crash-reports/*"
    ]

# Enable/disable debug mode here
DEBUG_MODE = True

def sanitize_filename(name):
    """Remove potentially dangerous characters from filenames"""
    return re.sub(r'[\\/*?:"<>|]', "", name)

def sanitize_command(cmd):
    """Command sanitization that allows safe characters"""
    # Allow letters, numbers, spaces, and safe punctuation
    return re.sub(r'[^a-zA-Z0-9 _\-.,:!@#$%^&*()+=]', '', cmd)

class SecureSettings:
    def __init__(self, password=None):
        self.cipher = self._get_cipher(password)
        
    def _get_cipher(self, password):
        if not os.path.exists(Constants.SECURITY_KEY_FILE):
            if password is None:
                # Generate random password for first-time setup
                password = secrets.token_urlsafe(32)
                with open(".master.key", "w") as f:
                    f.write(password)
                    if platform.system() != 'Windows':
                        os.chmod(".master.key", 0o600)
            
            # Derive key from password
            salt = os.urandom(16)
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=100000,
                backend=default_backend()
            )
            key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
            
            with open(Constants.SECURITY_KEY_FILE, 'wb') as f: 
                f.write(salt + b"::" + key)
            if platform.system() != 'Windows':
                os.chmod(Constants.SECURITY_KEY_FILE, 0o600)
        else:
            with open(Constants.SECURITY_KEY_FILE, 'rb') as f: 
                data = f.read()
            salt, key = data.split(b"::", 1)
            
            if password is None:
                # Try to read from master key file
                try:
                    with open(".master.key", "r") as f:
                        password = f.read().strip()
                except Exception:
                    raise RuntimeError("Password required for decryption")
            
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=100000,
                backend=default_backend()
            )
            key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        
        return Fernet(key)
    
    def encrypt(self, data):
        return self.cipher.encrypt(data.encode()).decode()
    
    def decrypt(self, encrypted_data):
        return self.cipher.decrypt(encrypted_data.encode()).decode()

# =============================================================================
# UTILITY CLASSES
# =============================================================================
class FileDownloader:
    """Robust file downloader with curl and requests fallback"""
    
    def __init__(self):
        self.max_retries = 5
        self.retry_delay_base = 1
        self.timeout = 600
    
    def download_file(self, url, path):
        """Download file with curl or requests fallback"""
        if self._try_curl_download(url, path):
            return path
        return self._requests_download(url, path)
    
    def _try_curl_download(self, url, path):
        try:
            subprocess.run(["curl", "--version"], capture_output=True, check=True)
            curl_cmd = [
                "curl", "-L", "-o", path, "-w", "%{http_code} %{size_download}",
                "-s", "-S", "--max-time", str(self.timeout), "--retry", "3",
                "--retry-delay", "2", "--fail", "--tlsv1.2", url
            ]
            
            result = subprocess.run(
                curl_cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=self.timeout + 30
            )
            
            output = result.stdout.strip()
            if not output:
                return False
                
            parts = output.split()
            if len(parts) < 2:
                return False
                
            http_code = parts[0]
            downloaded_size = int(parts[1])
            
            if not http_code.isdigit() or int(http_code) >= 400:
                return False
                
            if not os.path.exists(path):
                return False
                
            actual_size = os.path.getsize(path)
            if actual_size != downloaded_size:
                os.remove(path)
                return False
                
            return True
        except Exception:
            return False
            
    def _requests_download(self, url, path):
        for attempt in range(self.max_retries):
            try:
                with requests.get(url, stream=True, timeout=30, verify=True) as r:
                    r.raise_for_status()
                    with open(path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:  # filter out keep-alive chunks
                                f.write(chunk)
                return path
            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay_base * (2 ** attempt))
                else:
                    raise RuntimeError(f"Download failed: {str(e)}")

class BackupManager:
    """Handles server backups with proper directory exclusion"""
    
    def __init__(self, server_path):
        self.server_path = server_path
        self.backup_dir = os.path.join(server_path, Constants.BACKUP_DIR)
        
    def create_backup(self):
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = os.path.join(self.backup_dir, f"backup-{timestamp}.zip")
        os.makedirs(self.backup_dir, exist_ok=True)
        
        try:
            with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(self.server_path):
                    rel_root = os.path.relpath(root, self.server_path)
                    
                    # Skip backup directory
                    if rel_root == Constants.BACKUP_DIR:
                        continue
                    
                    # Skip excluded patterns
                    skip = False
                    for pattern in Constants.EXCLUDE_BACKUP_PATTERNS:
                        if fnmatch.fnmatch(rel_root, pattern):
                            skip = True
                            break
                    if skip:
                        continue
                    
                    for file in files:
                        # Skip excluded files
                        skip_file = False
                        full_path = os.path.join(root, file)
                        rel_file = os.path.join(rel_root, file)
                        
                        for pattern in Constants.EXCLUDE_BACKUP_PATTERNS:
                            if fnmatch.fnmatch(rel_file, pattern) or fnmatch.fnmatch(file, pattern):
                                skip_file = True
                                break
                        if skip_file:
                            continue
                            
                        arcname = os.path.relpath(full_path, self.server_path)
                        zipf.write(full_path, arcname)
            
            # Verify backup integrity
            with zipfile.ZipFile(backup_path, 'r') as zipf:
                if zipf.testzip() is not None:
                    raise RuntimeError("Backup verification failed")
                    
            return backup_path
        except Exception as e:
            logging.error(f"Backup failed: {str(e)}")
            # Clean up failed backup
            if os.path.exists(backup_path):
                os.remove(backup_path)
            raise

class ModrinthAPI:
    @lru_cache(maxsize=Constants.MAX_CACHE_SIZE)
    def get_versions(self, project_id):
        headers = {'User-Agent': Constants.HEADERS["DEFAULT_USER_AGENT"]}
        response = requests.get(
            f'https://api.modrinth.com/v2/project/{project_id}/version',
            headers=headers,
            timeout=10,
            verify=True
        )
        response.raise_for_status()
        return response.json()

    def search_mods(self, query, mc_version=None, loader=None, page=0):
        params = {
            'query': query,
            'limit': Constants.MODRINTH_PAGE_SIZE,
            'offset': page * Constants.MODRINTH_PAGE_SIZE
        }
        
        facets = []
        if mc_version:
            facets.append(f"versions:{mc_version}")
        if loader:
            facets.append(f"categories:{loader.lower()}")
        
        if facets:
            params['facets'] = json.dumps([facets])
        
        response = requests.get(
            Constants.API_ENDPOINTS["MODRINTH_SEARCH"],
            params=params,
            headers={'User-Agent': Constants.HEADERS["DEFAULT_USER_AGENT"]},
            timeout=15,
            verify=True
        )
        response.raise_for_status()
        return response.json()

class CurseForgeAPI:
    @lru_cache(maxsize=Constants.MAX_CACHE_SIZE)
    def get_file_info(self, file_id, api_key):
        headers = {'x-api-key': api_key}
        response = requests.get(
            f'https://api.curseforge.com/v1/mods/files/{file_id}',
            headers=headers,
            timeout=10,
            verify=True
        )
        response.raise_for_status()
        return response.json()

# =============================================================================
# THREAD WORKERS
# =============================================================================
class ModSearchThread(QThread):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, query, mc_version, loader, page=0):
        super().__init__()
        self.query = query
        self.mc_version = mc_version
        self.loader = loader
        self.page = page
        self.api = ModrinthAPI()

    def run(self):
        try:
            results = self.api.search_mods(
                self.query, 
                self.mc_version, 
                self.loader,
                self.page
            )
            
            formatted = []
            for hit in results['hits']:
                versions = self.api.get_versions(hit['project_id'])
                formatted.append({
                    'project_id': hit['project_id'],
                    'title': hit['title'],
                    'description': hit['description'],
                    'icon_url': hit.get('icon_url'),
                    'versions': versions
                })
                
            self.finished.emit(formatted)
        except Exception as e:
            self.error.emit(str(e))

class CurseForgeSearchThread(QThread):
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
                'gameId': 432,
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

class ImageLoaderThread(QThread):
    loaded = Signal(str, QPixmap)
    finished = Signal()

    def __init__(self, url, item_id):
        super().__init__()
        self.url = url
        self.item_id = item_id

    def run(self):
        try:
            response = requests.get(self.url, timeout=10, verify=True)
            img = QImage.fromData(response.content)
            pixmap = QPixmap.fromImage(img)
            self.loaded.emit(self.item_id, pixmap)
        except Exception:
            pixmap = QPixmap(80, 80)
            pixmap.fill(QColor(200, 200, 200))
            self.loaded.emit(self.item_id, pixmap)
        finally:
            self.finished.emit()

class UrlInstallThread(QThread):
    progress = Signal(int, str)
    finished = Signal()
    error = Signal(str)

    def __init__(self, urls, server_path, api_key, target_type):
        super().__init__()
        self.urls = urls
        self.server_path = server_path
        self.api_key = api_key
        self.target_type = target_type
        self.downloader = FileDownloader()
        self.cancelled = False

    def run(self):
        try:
            total = len(self.urls)
            success = 0
            target_dir = os.path.join(self.server_path, self.target_type)
            os.makedirs(target_dir, exist_ok=True)
            
            for i, url in enumerate(self.urls):
                if self.cancelled:
                    self.progress.emit(100, "Installation cancelled")
                    return
                    
                if not url.strip():
                    continue
                    
                self.progress.emit(int(100 * i / total), f"Processing URL {i+1}/{total}")
                
                try:
                    if "curseforge.com" in url:
                        filename = self.process_curseforge_url(url, target_dir)
                    else:
                        filename = self.download_direct(url, target_dir)
                    
                    success += 1
                    self.progress.emit(int(100 * (i+1) / total), f"Installed: {filename}")
                except Exception as e:
                    self.error.emit(f"URL {url} failed: {str(e)}")
            
            self.progress.emit(100, f"Successfully installed {success}/{total} items")
            self.finished.emit()
        except Exception as e:
            self.error.emit(f"Installation failed: {str(e)}")

    def cancel(self):
        self.cancelled = True

    def process_curseforge_url(self, url, target_dir):
        match = re.search(r'/projects/([^/]+)', url)
        if not match:
            raise ValueError("Invalid CurseForge URL format")
        
        project_slug = match.group(1)
        headers = {'x-api-key': self.api_key}
        
        search_url = f"https://api.curseforge.com/v1/mods/search?gameId=432&slug={project_slug}"
        response = requests.get(search_url, headers=headers, timeout=10, verify=True)
        response.raise_for_status()
        
        data = response.json()
        if not data['data']:
            raise ValueError("Project not found")
            
        project_id = data['data'][0]['id']
        
        files_url = f"https://api.curseforge.com/v1/mods/{project_id}/files"
        response = requests.get(files_url, headers=headers, timeout=10, verify=True)
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
        parsed = requests.utils.urlparse(url)
        filename = os.path.basename(parsed.path) or f"downloaded_{int(time.time())}.jar"
        # Sanitize filename
        filename = re.sub(r'[^\w\-\.]', '_', filename)
        dest_path = os.path.join(target_dir, filename)
        self.downloader.download_file(url, dest_path)
        return filename

class ServerThread(QThread):
    output = Signal(str)
    stopped = Signal()
    error = Signal(str)

    def __init__(self, command, cwd, java_path=None):
        super().__init__()
        self.command = command
        self.cwd = cwd
        self.java_path = java_path or "java"
        self.process = None
        self.running = False
        self.mutex = QMutex()

    def run(self):
        self.mutex.lock()
        self.running = True
        self.mutex.unlock()
        
        try:
            # Use the configured Java path
            full_command = [self.java_path] + self.command[1:]
            
            if DEBUG_MODE:
                print(f"[DEBUG] Starting server with command: {' '.join(full_command)}")
                print(f"[DEBUG] Working directory: {self.cwd}")
            
            # Create a clean environment
            env = os.environ.copy()
            env.update({
                "JAVA_HOME": os.path.dirname(self.java_path),
                "PATH": f"{os.path.dirname(self.java_path)}:{env.get('PATH', '')}"
            })
            
            if platform.system() == "Windows":
                command_str = " ".join(full_command)
                self.process = subprocess.Popen(
                    command_str,
                    cwd=self.cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                    shell=True,
                    env=env
                )
            else:
                self.process = subprocess.Popen(
                    full_command,
                    cwd=self.cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                    env=env
                )
                
            # Write PID file with restricted permissions
            pid_file = os.path.join(self.cwd, Constants.FILES["PID_FILE"])
            with open(pid_file, 'w') as f:
                f.write(str(self.process.pid))
            if platform.system() != 'Windows':
                os.chmod(pid_file, 0o600)
                
            if DEBUG_MODE:
                print(f"[DEBUG] Server process started with PID: {self.process.pid}")
                
            while self.is_running():
                output = self.process.stdout.readline()
                if output:
                    if DEBUG_MODE:
                        print(f"[SERVER OUTPUT] {output.strip()}")
                    self.output.emit(output.strip())
                if self.process.poll() is not None:
                    if DEBUG_MODE:
                        print(f"[DEBUG] Server process exited with code: {self.process.poll()}")
                    break
        except Exception as e:
            self.error.emit(f"Server thread error: {str(e)}")
            if DEBUG_MODE:
                print(f"[DEBUG] Server thread exception: {str(e)}")
        finally:
            self.stopped.emit()

    def is_running(self):
        self.mutex.lock()
        running = self.running
        self.mutex.unlock()
        return running

    def send_command(self, command):
        if self.process and self.process.stdin:
            try:
                self.process.stdin.write(command + "\n")
                self.process.stdin.flush()
                return True
            except Exception as e:
                self.error.emit(f"Failed to send command: {str(e)}")
        return False

    def stop(self):
        self.mutex.lock()
        self.running = False
        self.mutex.unlock()
        
        if self.process:
            try:
                if DEBUG_MODE:
                    print("[DEBUG] Stopping server process...")
                
                # Try graceful shutdown first
                self.send_command("stop")
                
                # Wait for process to exit
                if not self.wait_for_stop(30):
                    if DEBUG_MODE:
                        print("[DEBUG] Process did not stop, terminating...")
                    if platform.system() == "Windows":
                        subprocess.run(f"taskkill /F /PID {self.process.pid}", 
                                      shell=True, check=False)
                    else:
                        os.kill(self.process.pid, signal.SIGTERM)
                    
                    if not self.wait_for_stop(5):
                        if DEBUG_MODE:
                            print("[DEBUG] Process did not terminate, killing...")
                        if platform.system() == "Windows":
                            subprocess.run(f"taskkill /F /PID {self.process.pid}", 
                                          shell=True, check=False)
                        else:
                            os.kill(self.process.pid, signal.SIGKILL)
            except Exception as e:
                self.error.emit(f"Error stopping process: {str(e)}")
                if DEBUG_MODE:
                    print(f"[DEBUG] Error stopping process: {str(e)}")
            finally:
                pid_file = os.path.join(self.cwd, Constants.FILES["PID_FILE"])
                if os.path.exists(pid_file):
                    try:
                        os.remove(pid_file)
                        if DEBUG_MODE:
                            print("[DEBUG] Removed PID file")
                    except Exception:
                        pass

    def wait_for_stop(self, timeout):
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.process.poll() is not None:
                return True
            time.sleep(0.5)
        return False

# =============================================================================
# DIALOG CLASSES
# =============================================================================
class VersionSelectDialog(QDialog):
    def __init__(self, versions, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Version")
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout(self)
        self.version_combo = QComboBox()
        
        for version in versions:
            game_versions = ", ".join(version.get('game_versions', ['Unknown']))
            self.version_combo.addItem(
                f"{version['version_number']} (MC: {game_versions})", 
                userData=version
            )
        
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        
        layout.addWidget(QLabel("Select version to install:"))
        layout.addWidget(self.version_combo)
        layout.addWidget(button_box)
    
    def selected_version(self):
        return self.version_combo.currentData()

# =============================================================================
# MAIN APPLICATION
# =============================================================================
class ServerManager(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.java_versions = Constants.JAVA_VERSIONS
        self.loaders = Constants.LOADERS
        self.servers = {}
        self.current_server = None
        self.current_image_loaders = []
        self.api_keys = {'curseforge': '', 'java_paths': {}}
        self.secure_settings = self.initialize_secure_settings()
        self.console_buffer = []
        self.state_mutex = QMutex()
        self.server_start_time = None
        self.start_timeout_timer = QTimer(self)
        
        self.init_ui()
        self.load_profiles()
        self.check_java()
        self.check_api_keys()
        self.setup_logging()
        self.setStyleSheet(self.get_style_sheet())
        self.statusBar().showMessage("Ready")
        
        # Setup timers
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.update_server_status)
        self.status_timer.start(2000)
        
        self.console_update_timer = QTimer(self)
        self.console_update_timer.timeout.connect(self.update_console_display)
        self.console_update_timer.setSingleShot(True)
        
        self.start_timeout_timer.timeout.connect(self.check_start_timeout)
        self.start_timeout_timer.setSingleShot(True)

    def initialize_secure_settings(self):
        """Initialize secure settings with password protection"""
        try:
            return SecureSettings()
        except Exception as e:
            password, ok = QInputDialog.getText(
                self, "Encryption Password", 
                "Enter password for settings decryption:",
                QLineEdit.Password
            )
            if ok and password:
                return SecureSettings(password)
            else:
                QMessageBox.critical(
                    self, "Encryption Error", 
                    "Failed to initialize secure settings: " + str(e)
                )
                sys.exit(1)

    def setup_logging(self):
        """Configure robust logging with rotation"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.handlers.RotatingFileHandler(  # Fixed handler reference
                    'server_manager.log',
                    maxBytes=5*1024*1024,  # 5 MB
                    backupCount=3,
                    encoding='utf-8'
                )
            ]
        )

    def get_style_sheet(self):
        """Return UI styling"""
        return """
            QTextEdit, QLineEdit, QComboBox { padding: 3px; margin: 1px; }
            QPushButton { min-height: 25px; margin: 2px; }
            QTabWidget::pane { border: 1px solid #444; margin: 2px; }
            QProgressBar { text-align: center; }
            QLabel { margin: 2px; }
            QTreeView { font-family: monospace; }
            QScrollArea { background: transparent; }
        """

    def init_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle("Minecraft Server Manager")
        self.setGeometry(100, 100, 1200, 800)
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # Left Panel - Server List
        server_list_panel = QWidget()
        server_list_layout = QVBoxLayout(server_list_panel)
        self.setup_server_list(server_list_layout)
        main_layout.addWidget(server_list_panel, stretch=1)

        # Right Panel - Content
        content_panel = QWidget()
        content_layout = QVBoxLayout(content_panel)
        self.setup_creation_form(content_layout)
        self.setup_tabs(content_layout)
        main_layout.addWidget(content_panel, stretch=3)

        self.setup_shortcuts()

    def setup_server_list(self, layout):
        """Setup server list panel"""
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
        """Setup server creation form"""
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
        """Setup main tabs"""
        self.tabs = QTabWidget()
        
        # Console Tab
        console_tab = QWidget()
        self.setup_console_tab(console_tab)
        self.tabs.addTab(console_tab, "Console")

        # File Browser Tab
        file_tab = QWidget()
        self.setup_file_tab(file_tab)
        self.tabs.addTab(file_tab, "Files")

        # Properties Editor Tab
        properties_tab = QWidget()
        self.setup_properties_tab(properties_tab)
        self.tabs.addTab(properties_tab, "Properties")

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
        """Setup console tab"""
        layout = QVBoxLayout(parent)
        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        self.console_output.setFont(QFont("Courier New", 10))
        
        input_container = QWidget()
        input_layout = QHBoxLayout(input_container)
        input_layout.setContentsMargins(0, 0, 0, 0)
        
        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("Enter server command...")
        self.command_input.returnPressed.connect(self.send_command)
        
        btn_send = QPushButton("Send")
        btn_send.clicked.connect(self.send_command)
        
        input_layout.addWidget(self.command_input, 4)
        input_layout.addWidget(btn_send, 1)
        
        layout.addWidget(self.console_output)
        layout.addWidget(input_container)
        self.command_input.setFocusPolicy(Qt.StrongFocus)
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(
            lambda: self.command_input.setFocus()
        )
        self.tabs.currentChanged.connect(self.on_tab_changed)

    def setup_file_tab(self, parent):
        """Setup file browser tab"""
        layout = QVBoxLayout(parent)
        self.file_model = QFileSystemModel()
        self.file_model.setRootPath(QDir.homePath())
        self.file_view = QTreeView()
        self.file_view.setModel(self.file_model)
        self.file_view.doubleClicked.connect(self.open_file)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.refresh_file_view)
        layout.addWidget(self.file_view)
        layout.addWidget(btn_refresh)

    def setup_properties_tab(self, parent):
        """Setup properties editor tab"""
        layout = QVBoxLayout(parent)
        self.properties_editor = QTextEdit()
        self.properties_editor.setFont(QFont("Courier New", 10))
        self.properties_editor.setPlaceholderText("server.properties content will appear here...")
        
        btn_save = QPushButton("Save Properties")
        btn_save.clicked.connect(self.save_server_properties)
        
        layout.addWidget(QLabel("Edit server.properties:"))
        layout.addWidget(self.properties_editor)
        layout.addWidget(btn_save)

    def setup_mods_tab(self, parent):
        """Setup mods management tab"""
        layout = QVBoxLayout(parent)
        layout.addLayout(self.create_url_section("mods"))
        
        platform_layout = QHBoxLayout()
        self.mods_platform_combo = QComboBox()
        self.mods_platform_combo.addItems(["Modrinth", "CurseForge"])
        platform_layout.addWidget(QLabel("Source:"))
        platform_layout.addWidget(self.mods_platform_combo)
        layout.addLayout(platform_layout)

        search_layout = QHBoxLayout()
        self.mod_search = QLineEdit()
        self.mod_search.setPlaceholderText("Search mods...")
        btn_search = QPushButton("Search")
        btn_search.clicked.connect(self.safe_mod_search)
        search_layout.addWidget(self.mod_search)
        search_layout.addWidget(btn_search)
        layout.addLayout(search_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.mod_list_container = QWidget()
        self.mod_list_layout = QVBoxLayout(self.mod_list_container)
        scroll.setWidget(self.mod_list_container)
        layout.addWidget(scroll)
        
        btn_update = QPushButton("Check for Updates")
        btn_update.clicked.connect(self.check_mod_updates)
        layout.addWidget(btn_update)

    def setup_plugins_tab(self, parent):
        """Setup plugins management tab"""
        layout = QVBoxLayout(parent)
        layout.addLayout(self.create_url_section("plugins"))
        
        platform_layout = QHBoxLayout()
        self.plugins_platform_combo = QComboBox()
        self.plugins_platform_combo.addItems(["CurseForge", "Modrinth"])
        platform_layout.addWidget(QLabel("Source:"))
        platform_layout.addWidget(self.plugins_platform_combo)
        layout.addLayout(platform_layout)

        search_layout = QHBoxLayout()
        self.plugin_search = QLineEdit()
        self.plugin_search.setPlaceholderText("Search plugins...")
        btn_search = QPushButton("Search")
        btn_search.clicked.connect(self.safe_plugin_search)
        search_layout.addWidget(self.plugin_search)
        search_layout.addWidget(btn_search)
        layout.addLayout(search_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.plugin_list_container = QWidget()
        self.plugin_list_layout = QVBoxLayout(self.plugin_list_container)
        scroll.setWidget(self.plugin_list_container)
        layout.addWidget(scroll)

    def setup_settings_tab(self, parent):
        """Setup settings tab"""
        layout = QGridLayout(parent)
        
        # API Key Section
        layout.addWidget(QLabel("CurseForge API Key:"), 0, 0)
        self.curseforge_key_input = QLineEdit()
        self.curseforge_key_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.curseforge_key_input, 0, 1)
        
        # Java Path Configuration
        layout.addWidget(QLabel("Java Paths:"), 1, 0, 1, 2)
        
        self.java_path_table = QTreeView()
        self.java_path_model = QStandardItemModel()
        self.java_path_model.setHorizontalHeaderLabels(["Version", "Path"])
        self.java_path_table.setModel(self.java_path_model)
        self.java_path_table.setRootIsDecorated(False)
        layout.addWidget(self.java_path_table, 2, 0, 1, 2)
        
        btn_add_java = QPushButton("Add Java Path")
        btn_add_java.clicked.connect(self.add_java_path)
        layout.addWidget(btn_add_java, 3, 0)
        
        btn_remove_java = QPushButton("Remove Selected")
        btn_remove_java.clicked.connect(self.remove_java_path)
        layout.addWidget(btn_remove_java, 3, 1)
        
        # Save Button
        btn_save = QPushButton("Save Settings")
        btn_save.clicked.connect(self.save_api_keys)
        layout.addWidget(btn_save, 4, 0, 1, 2)

    def create_url_section(self, target_type):
        """Create URL install section for mods/plugins"""
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
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.cancel_url_install)
        
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(install_btn)
        btn_layout.addWidget(cancel_btn)
        
        url_layout.addWidget(QLabel(f"Install from URLs:"))
        url_layout.addWidget(url_input)
        url_layout.addWidget(progress)
        url_layout.addWidget(status)
        url_layout.addLayout(btn_layout)
        
        setattr(self, f"{target_type}_url_input", url_input)
        setattr(self, f"{target_type}_progress", progress)
        setattr(self, f"{target_type}_status", status)
        
        return url_layout

    def setup_shortcuts(self):
        """Setup keyboard shortcuts"""
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self.refresh_file_view)
        QShortcut(QKeySequence("Ctrl+Q"), self).activated.connect(self.close)
        QShortcut(QKeySequence("Ctrl+B"), self).activated.connect(self.create_backup)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.save_server_properties)

    def check_java(self):
        """Check for Java installations"""
        try:
            # Try to detect Java from configured paths first
            java_paths = self.api_keys.get('java_paths', {})
            for version, path in java_paths.items():
                if os.path.exists(path):
                    self.java_combo.setCurrentText(version)
                    return
            
            # Run java -version and capture output
            result = subprocess.run(
                ['java', '-version'],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Java outputs version to stderr
                text=True,
                check=False,
                timeout=5
            )
            
            version_info = result.stdout or result.stderr
            
            if DEBUG_MODE:
                print(f"Java version output: {repr(version_info)}")
                logging.info(f"Java version output: {repr(version_info)}")
            
            # Robust regex for various Java version formats
            version_match = re.search(
                r'version\s+"?(\d+(?:\.\d+)*[^"\s]*)', 
                version_info, 
                re.IGNORECASE
            )
            
            if version_match:
                version_str = version_match.group(1)
                if DEBUG_MODE:
                    print(f"Extracted version string: {version_str}")
                
                # Extract major version
                if version_str.startswith("1."):
                    major_version = version_str.split('.')[1]
                else:
                    major_version = version_str.split('.')[0]
                
                # Clean up non-digit characters
                major_version = ''.join(filter(str.isdigit, major_version))
                
                if DEBUG_MODE:
                    print(f"Extracted major version: {major_version}")
                
                if major_version in self.java_versions:
                    self.java_combo.setCurrentText(major_version)
                    if DEBUG_MODE:
                        print(f"Java version {major_version} selected")
                    return
                else:
                    if DEBUG_MODE:
                        print(f"Java version {major_version} not in supported list: {self.java_versions}")
            
            # Fallback to first Java version in the list
            self.java_combo.setCurrentIndex(0)
            self.show_info("Java installation found but version could not be determined. Using default.")
            
        except Exception as e:
            # Java command not found
            self.show_error(f"Java runtime not found! Install Java 8+ first. Error: {str(e)}")
            if DEBUG_MODE:
                print(f"Java check error: {str(e)}")
            # Fallback to first Java version in the list
            self.java_combo.setCurrentIndex(0)

    def refresh_file_view(self):
        """Refresh file browser view"""
        if self.current_server:
            server_path = self.servers[self.current_server]['path']
            self.file_view.setRootIndex(self.file_model.index(server_path))

    def load_profiles(self):
        """Load server profiles from disk"""
        try:
            if os.path.exists(Constants.FILES["PROFILES"]):
                with open(Constants.FILES["PROFILES"], 'r') as f:
                    self.servers = json.load(f)
                    for server in self.servers.values():
                        server['thread'] = None
                        server['status'] = Constants.SERVER_STATUS["STOPPED"] 
                    self.update_server_list()
        except Exception as e:
            self.show_error(f"Failed to load profiles: {str(e)}")

    def save_profiles(self):
        """Save server profiles to disk"""
        try:
            with open(Constants.FILES["PROFILES"], 'w') as f:
                save_data = {name: {k:v for k,v in data.items() if k != 'thread'} 
                           for name, data in self.servers.items()}
                json.dump(save_data, f, indent=2)
        except Exception as e:
            self.show_error(f"Failed to save profiles: {str(e)}")

    def check_api_keys(self):
        """Load API keys from secure storage"""
        try:
            if os.path.exists(Constants.FILES["SETTINGS"]):
                with open(Constants.FILES["SETTINGS"], 'r') as f:
                    encrypted = f.read()
                    self.api_keys = json.loads(self.secure_settings.decrypt(encrypted))
                    self.curseforge_key_input.setText(self.api_keys.get('curseforge', ''))
                    self.update_java_path_model()
        except Exception as e:
            logging.error(f"Secure load failed: {str(e)}")
    
    def save_api_keys(self):
        """Save API keys to secure storage"""
        self.api_keys['curseforge'] = self.curseforge_key_input.text()
        try:
            encrypted = self.secure_settings.encrypt(json.dumps(self.api_keys))
            with open(Constants.FILES["SETTINGS"], 'w') as f:
                f.write(encrypted)
            self.statusBar().showMessage("Settings saved", 3000)
        except Exception as e:
            logging.error(f"Settings save failed: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to save settings")

    def update_java_path_model(self):
        """Update Java path table model"""
        self.java_path_model.clear()
        self.java_path_model.setHorizontalHeaderLabels(["Version", "Path"])
        for version, path in self.api_keys.get('java_paths', {}).items():
            version_item = QStandardItem(version)
            path_item = QStandardItem(path)
            self.java_path_model.appendRow([version_item, path_item])

    def add_java_path(self):
        """Add new Java path to configuration"""
        version, ok1 = QInputDialog.getText(self, "Java Version", "Enter Java version (e.g., 8, 11, 17):")
        if not ok1 or not version:
            return
            
        path, ok2 = QFileDialog.getOpenFileName(
            self, "Select Java Executable", 
            "/usr/bin" if platform.system() != "Windows" else "C:\\Program Files\\Java",
            "Executable Files (*.exe)" if platform.system() == "Windows" else ""
        )
        if not ok2 or not path:
            return
            
        self.api_keys.setdefault('java_paths', {})[version] = path
        self.update_java_path_model()

    def remove_java_path(self):
        """Remove selected Java path from configuration"""
        selected = self.java_path_table.selectionModel().selectedIndexes()
        if not selected:
            return
            
        row = selected[0].row()
        version_item = self.java_path_model.item(row, 0)
        if version_item:
            version = version_item.text()
            if version in self.api_keys.get('java_paths', {}):
                del self.api_keys['java_paths'][version]
                self.update_java_path_model()

    def show_info(self, message):
        """Show informational message"""
        QMessageBox.information(self, "Success", message)
        self.statusBar().showMessage(message, 3000)

    def show_error(self, message):
        """Show error message"""
        QMessageBox.critical(self, "Error", message)
        self.statusBar().showMessage(f"Error: {message}", 5000)

    def show_search_error(self, message):
        """Show search error message"""
        self.progress.hide()
        self.show_error(f"Search error: {message}")

    def select_server_directory(self):
        """Select server directory through dialog"""
        path = QFileDialog.getExistingDirectory(
            self, "Select Directory", QDir.homePath(), QFileDialog.ShowDirsOnly
        )
        if path:
            self.server_path.setText(path)

    def create_new_server(self):
        """Create a new server"""
        if not self.server_path.text():
            self.show_error("Select server directory first!")
            return
        
        name, ok = QInputDialog.getText(self, "Server Name", "Enter server name:")
        if ok and name:
            try:
                self.setup_server(name)
            except ValueError as e:
                self.show_error(str(e))

    def setup_server(self, name):
        """Setup server files and configuration"""
        base_dir = self.server_path.text()
        if not base_dir:
            base_dir = QDir.homePath()
        
        # Sanitize and create directory
        sanitized_name = sanitize_filename(name)
        server_dir = os.path.join(base_dir, sanitized_name)
        
        try:
            os.makedirs(server_dir, exist_ok=True)
            if platform.system() != "Windows":
                os.chmod(server_dir, 0o755)
                
            loader = self.loader_combo.currentText()
            version = self.version_combo.currentText()
            jar_path = os.path.join(server_dir, Constants.FILES["SERVER_JAR"])

            downloader = FileDownloader()
            if loader == "Vanilla":
                downloader.download_file(self.get_vanilla_url(version), jar_path)
            elif loader == "Paper":
                downloader.download_file(self.get_paper_url(version), jar_path)
            else:
                raise ValueError(f"Unsupported loader: {loader}")

            self.create_server_properties(server_dir)
            self.create_eula_file(server_dir)

            self.servers[name] = {
                "path": server_dir,
                "loader": loader,
                "status": Constants.SERVER_STATUS["STOPPED"],
                "max_ram": "2G",
                "mc_version": version,
                "thread": None
            }
            self.save_profiles()
            self.update_server_list()
            self.current_server = name
            self.file_model.setRootPath(server_dir)
            self.file_view.setRootIndex(self.file_model.index(server_dir))
            self.load_server_properties()
            self.show_info(f"Server '{name}' created successfully!")
            
        except Exception as e:
            error_msg = f"Server creation failed: {str(e)}"
            logging.exception(error_msg)
            self.show_error(error_msg)
            if os.path.exists(server_dir):
                try:
                    shutil.rmtree(server_dir, ignore_errors=True)
                except Exception as cleanup_error:
                    logging.error(f"Cleanup failed: {str(cleanup_error)}")

    def create_eula_file(self, path):
        """Create EULA file"""
        with open(os.path.join(path, Constants.FILES["EULA_FILE"]), 'w') as f:
            f.write("eula=true\n")

    def update_versions(self, loader_name):
        """Update available versions for selected loader"""
        self.version_combo.clear()
        self.progress.show()
        self.statusBar().showMessage("Fetching versions...")

        try:
            if loader_name == "Vanilla":
                response = requests.get(Constants.API_ENDPOINTS["VANILLA_MANIFEST"], timeout=10)
                versions = [v['id'] for v in response.json()['versions'] if v['type'] == 'release']
            elif loader_name == "Paper":
                response = requests.get(Constants.API_ENDPOINTS["PAPER_VERSIONS"], timeout=10)
                paper_data = response.json()
                valid_versions = [
                    v for v in reversed(paper_data['versions'])
                    if re.match(r'^\d+\.\d+(\.\d+)?$', v)  # Improved regex
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
        """Get download URL for Vanilla server"""
        manifest = requests.get(Constants.API_ENDPOINTS["VANILLA_MANIFEST"]).json()
        for v in manifest['versions']:
            if v['id'] == version and v['type'] == "release":
                version_data = requests.get(v['url']).json()
                return version_data['downloads']['server']['url']
        raise ValueError("Version not found")

    def get_paper_url(self, version):
        """Get download URL for Paper server"""
        try:
            builds_url = f"{Constants.API_ENDPOINTS['PAPER_VERSIONS']}/versions/{version}/builds"
            response = requests.get(builds_url, timeout=10)
            response.raise_for_status()
            builds_data = response.json()
    
            if not builds_data.get('builds'):
                raise ValueError(f"No builds available for PaperMC {version}")
    
            latest_build = builds_data['builds'][-1]
            build_number = latest_build['build']
            downloads_data = latest_build['downloads']['application']
            filename = downloads_data['name']
            
            return (
                f"{Constants.API_ENDPOINTS['PAPER_VERSIONS']}/"
                f"versions/{version}/"
                f"builds/{build_number}/"
                f"downloads/{filename}"
            )
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise ValueError(f"PaperMC version {version} not found")
            raise ValueError(f"API request failed: {str(e)}")
        except KeyError as e:
            raise ValueError(f"Missing required field in API response: {str(e)}")
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON response from PaperMC API")

    def update_server_list(self):
        """Update server list display"""
        self.server_list.clear()
        for server_name, data in self.servers.items():
            item = QListWidgetItem(server_name)
            status = data.get('status', Constants.SERVER_STATUS["STOPPED"])
            if status == Constants.SERVER_STATUS["RUNNING"]:
                item.setForeground(Qt.green)
            elif status == Constants.SERVER_STATUS["STARTING"]:
                item.setForeground(Qt.yellow)
            elif status == Constants.SERVER_STATUS["STOPPING"]:
                item.setForeground(QColor(255, 165, 0))
            else:
                item.setForeground(Qt.red)
            self.server_list.addItem(item)

    def select_server(self, item):
        """Select a server from the list"""
        self.current_server = item.text()
        if self.current_server in self.servers:
            server_data = self.servers[self.current_server]
            self.file_model.setRootPath(server_data['path'])
            self.file_view.setRootIndex(self.file_model.index(server_data['path']))
            self.console_output.clear()
            self.console_buffer = []
            self.load_server_properties()

    def on_tab_changed(self, index):
        """Handle tab change events"""
        if self.tabs.tabText(index) == "Console":
            self.command_input.setFocus()

    def send_command(self):
        """Send command to server"""
        cmd = self.command_input.text().strip()
        if not cmd or not self.current_server:
            return
            
        # Sanitize command input
        cmd = sanitize_command(cmd)
        server = self.servers[self.current_server]
        
        if server['status'] == Constants.SERVER_STATUS["RUNNING"]:
            if server['thread'] and server['thread'].send_command(cmd):
                self.console_buffer.append(f"> {cmd}")
                self.update_console_display()
                self.command_input.clear()
            else:
                self.show_error("Failed to send command to server process")
        else:
            self.show_error("Server is not running")

    def update_console_display(self):
        """Update console display with buffered output"""
        self.console_output.setPlainText("\n".join(self.console_buffer[-Constants.MAX_CONSOLE_LINES:]))
        self.console_output.verticalScrollBar().setValue(
            self.console_output.verticalScrollBar().maximum()
        )
        self.console_update_timer.stop()

    def update_server_status(self):
        """Periodically update server status"""
        if not self.current_server:
            return
            
        server = self.servers[self.current_server]
        path = server['path']
        
        if server.get('status') == Constants.SERVER_STATUS["STARTING"]:
            if not self.is_server_process_running(path):
                server['status'] = Constants.SERVER_STATUS["STOPPED"]
                self.update_server_list()
                self.statusBar().showMessage("Server failed to start", 5000)
        elif server.get('status') == Constants.SERVER_STATUS["RUNNING"]:
            if not self.is_server_process_running(path):
                server['status'] = Constants.SERVER_STATUS["STOPPED"]
                self.update_server_list()
                self.statusBar().showMessage("Server stopped unexpectedly", 5000)
        elif server.get('status') == Constants.SERVER_STATUS["STOPPING"]:
            if not self.is_server_process_running(path):
                server['status'] = Constants.SERVER_STATUS["STOPPED"]
                self.update_server_list()
                self.statusBar().showMessage("Server stopped", 3000)

    def is_server_process_running(self, server_path):
        """Check if server process is running"""
        pid_file = os.path.join(server_path, Constants.FILES["PID_FILE"])
        if not os.path.exists(pid_file):
            return False
            
        try:
            with open(pid_file, 'r') as f:
                pid = int(f.read().strip())
            
            if platform.system() == "Windows":
                result = subprocess.run(
                    f'tasklist /FI "PID eq {pid}"', 
                    capture_output=True, 
                    text=True,
                    shell=True,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                return str(pid) in result.stdout
            else:
                try:
                    os.kill(pid, 0)  # Check if process exists
                    return True
                except OSError:
                    return False
        except Exception:
            return False

    def start_server(self):
        """Start the selected server"""
        if not self.current_server:
            return
        
        server = self.servers[self.current_server]
        if server['status'] == Constants.SERVER_STATUS["STOPPED"]:
            try:
                jar_path = os.path.join(server['path'], Constants.FILES["SERVER_JAR"])
                if not os.path.exists(jar_path):
                    raise FileNotFoundError(f"Server JAR not found at {jar_path}")
                
                # Get configured Java path or default
                java_version = server.get('java_version', self.java_combo.currentText())
                java_path = self.api_keys.get('java_paths', {}).get(java_version, "java")
                
                command = [
                    java_path,
                    f"-Xmx{server.get('max_ram', '2G')}",
                    "-jar",
                    jar_path,
                    "nogui"
                ]
                
                # Log the command for debugging
                logging.info(f"Starting server with command: {' '.join(command)}")
                if DEBUG_MODE:
                    print(f"[DEBUG] Starting server: {' '.join(command)}")
                
                server['thread'] = ServerThread(command, server['path'], java_path)
                server['thread'].output.connect(self.handle_server_output)
                server['thread'].stopped.connect(self.handle_server_stop)
                server['thread'].error.connect(self.handle_thread_error)
                server['thread'].start()
                
                # Update status to STARTING
                server['status'] = Constants.SERVER_STATUS["STARTING"]
                self.save_profiles()
                self.update_server_list()
                self.statusBar().showMessage("Server starting...")
                if DEBUG_MODE:
                    print(f"[DEBUG] Server status set to STARTING for {self.current_server}")
                
                # Set start time and start timeout timer (2 minutes)
                self.server_start_time = time.time()
                self.start_timeout_timer.start(120000)  # 120,000 ms = 2 minutes
                if DEBUG_MODE:
                    print("[DEBUG] Started server start timeout timer (2 minutes)")
                
            except Exception as e:
                server['status'] = Constants.SERVER_STATUS["STOPPED"]
                self.show_error(f"Start failed: {str(e)}")
                logging.error(f"Start failed: {str(e)}")
                if DEBUG_MODE:
                    print(f"[DEBUG] Server start failed: {str(e)}")

    def check_start_timeout(self):
        """Check if server start has timed out"""
        if self.current_server:
            server = self.servers[self.current_server]
            if server['status'] == Constants.SERVER_STATUS["STARTING"]:
                # If still in starting state after timeout, mark as stopped
                elapsed = time.time() - self.server_start_time
                logging.warning(f"Server start timed out after {elapsed:.1f} seconds")
                
                server['status'] = Constants.SERVER_STATUS["STOPPED"]
                self.save_profiles()
                self.update_server_list()
                self.statusBar().showMessage("Server start timed out", 5000)
                
                if DEBUG_MODE:
                    print(f"[DEBUG] Server start timed out after {elapsed:.1f} seconds")
                
                # Stop the server thread if it exists
                if server.get('thread'):
                    server['thread'].stop()

    def handle_server_output(self, message):
        """Handle output from server process"""
        self.console_buffer.append(message)
        if not self.console_update_timer.isActive():
            self.console_update_timer.start(500)
        
        if self.current_server:
            server = self.servers[self.current_server]
            
            # Create a combined view of the last few messages
            recent_messages = "\n".join(self.console_buffer[-5:])
            
            if server['status'] == Constants.SERVER_STATUS["STARTING"]:
                loader = server.get('loader', '').lower()
                
                # Check for any completion indicators
                completion_indicators = [
                    "Done (", 
                    "! For help, type \"help\"",
                    "For help, type \"help\"",
                    "Listening on",
                    "MinecraftForge",
                    "Quilt Mod Loader version",
                    "Forge Mod Loader version"
                ]
                
                # Check both the current message and the last few messages combined
                for indicator in completion_indicators:
                    if indicator in recent_messages:
                        if DEBUG_MODE:
                            print(f"[DEBUG] SERVER READY DETECTED BY INDICATOR: {indicator}")
                        server['status'] = Constants.SERVER_STATUS["RUNNING"]
                        self.save_profiles()
                        self.update_server_list()
                        self.statusBar().showMessage("Server is running", 3000)
                        
                        # Stop the timeout timer
                        if self.start_timeout_timer.isActive():
                            self.start_timeout_timer.stop()
                            if DEBUG_MODE:
                                print("[DEBUG] Stopped server start timeout timer")
                        break
            
            # Error detection
            if any(e in message for e in ["ERROR", "Exception", "Crash"]):
                logging.error(f"Server Error: {message}")
                if DEBUG_MODE:
                    print(f"[DEBUG] Server error detected: {message}")
                    
                if server['status'] == Constants.SERVER_STATUS["STARTING"]:
                    if DEBUG_MODE:
                        print("[DEBUG] SERVER START FAILED DUE TO ERROR")
                    server['status'] = Constants.SERVER_STATUS["STOPPED"]
                    self.save_profiles()
                    self.update_server_list()
                    self.statusBar().showMessage("Server failed to start", 5000)
                    
                    # Stop the timeout timer
                    if self.start_timeout_timer.isActive():
                        self.start_timeout_timer.stop()

    def handle_thread_error(self, message):
        """Handle errors from server thread"""
        if self.current_server:
            server = self.servers[self.current_server]
            server['status'] = Constants.SERVER_STATUS["STOPPED"]
            self.update_server_list()
            self.show_error(f"Server error: {message}")
            logging.error(f"Server thread error: {message}")
            if DEBUG_MODE:
                print(f"[DEBUG] Server thread error: {message}")

    def handle_server_stop(self):
        """Handle server stop event"""
        if self.current_server:
            server = self.servers[self.current_server]
            server['status'] = Constants.SERVER_STATUS["STOPPED"]
            server['thread'] = None
            self.save_profiles()
            self.update_server_list()
            self.statusBar().showMessage("Server stopped", 3000)
            if DEBUG_MODE:
                print(f"[DEBUG] Server stopped: {self.current_server}")

    def stop_server(self):
        """Stop the selected server"""
        if self.current_server and self.servers[self.current_server]['status'] in [
            Constants.SERVER_STATUS["RUNNING"], 
            Constants.SERVER_STATUS["STARTING"]
        ]:
            try:
                server = self.servers[self.current_server]
                if server['thread']:
                    server['thread'].stop()
                server['status'] = Constants.SERVER_STATUS["STOPPING"]
                self.save_profiles()
                self.update_server_list()
                self.statusBar().showMessage("Stopping server...")
                if DEBUG_MODE:
                    print(f"[DEBUG] Stopping server: {self.current_server}")
            except Exception as e:
                self.show_error(f"Stop failed: {str(e)}")
                if DEBUG_MODE:
                    print(f"[DEBUG] Stop failed: {str(e)}")

    def delete_server(self):
        """Delete the selected server"""
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
                if self.servers[self.current_server]['status'] != Constants.SERVER_STATUS["STOPPED"]:
                    self.stop_server()
                    time.sleep(2)  # Give it a moment to stop
                
                if os.path.exists(server_path):
                    shutil.rmtree(server_path, ignore_errors=True)
                del self.servers[self.current_server]
                self.current_server = None
                self.save_profiles()
                self.update_server_list()
                self.statusBar().showMessage("Server deleted", 3000)
                if DEBUG_MODE:
                    print(f"[DEBUG] Deleted server: {server_path}")
            except Exception as e:
                self.show_error(f"Delete failed: {str(e)}")
                if DEBUG_MODE:
                    print(f"[DEBUG] Delete failed: {str(e)}")

    def create_backup(self):
        """Create server backup"""
        if not self.current_server:
            return
            
        try:
            server_path = self.servers[self.current_server]['path']
            backup_path = BackupManager(server_path).create_backup()
            self.show_info(f"Backup created: {os.path.basename(backup_path)}")
        except Exception as e:
            self.show_error(f"Backup failed: {str(e)}")

    def open_file(self, index):
        """Open selected file"""
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
        """Create server.properties file with secure defaults"""
        properties_path = os.path.join(server_dir, "server.properties")
        rcon_password = secrets.token_urlsafe(16)  # Secure RCON password
        
        with open(properties_path, 'w') as f:
            f.write("# Minecraft server properties\n")
            f.write("enable-jmx-monitoring=false\n")
            f.write(f"rcon.port=25575\n")
            f.write(f"rcon.password={rcon_password}\n")  # Secure password
            f.write("enable-rcon=false\n")  # Disabled by default
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
            f.write("online-mode=true\n")  # Enabled by default for security
            f.write("enable-status=true\n")
            f.write("allow-flight=false\n")
            f.write("broadcast-rcon-to-ops=true\n")
            f.write("view-distance=16\n")
            f.write("server-ip=\n")
            f.write("resource-pack-prompt=\n")
            f.write("allow-nether=true\n")
            f.write("server-port=25565\n")
            f.write("sync-chunk-writes=true\n")
            f.write("op-permission-level=4\n")
            f.write("prevent-proxy-connections=true\n")  # Security enhancement
            f.write("hide-online-players=false\n")
            f.write("resource-pack=\n")
            f.write("entity-broadcast-range-percentage=100\n")
            f.write("simulation-distance=16\n")
            f.write("player-idle-timeout=0\n")
            f.write("debug=false\n")
            f.write("force-gamemode=true\n")
            f.write("rate-limit=0\n")
            f.write("hardcore=false\n")
            f.write("white-list=true\n")  # Enabled by default
            f.write("broadcast-console-to-ops=true\n")
            f.write("spawn-npcs=true\n")
            f.write("spawn-animals=true\n")
            f.write("snooper-enabled=false\n")  # Privacy enhancement
            f.write("function-permission-level=2\n")
            f.write("level-type=default\n")
            f.write("text-filtering-config=\n")
            f.write("spawn-monsters=true\n")
            f.write("enforce-whitelist=false\n")
            f.write("resource-pack-sha1=\n")
            f.write("spawn-protection=16\n")  # Reasonable default
            f.write("max-world-size=29999984\n")

    def load_server_properties(self):
        """Load server.properties into editor"""
        if not self.current_server:
            return
            
        server = self.servers[self.current_server]
        prop_file = os.path.join(server['path'], "server.properties")
        
        if os.path.exists(prop_file):
            try:
                with open(prop_file, 'r') as f:
                    self.properties_editor.setPlainText(f.read())
            except Exception as e:
                self.show_error(f"Failed to load properties: {str(e)}")

    def save_server_properties(self):
        """Save server.properties from editor"""
        if not self.current_server:
            return
            
        server = self.servers[self.current_server]
        prop_file = os.path.join(server['path'], "server.properties")
        
        try:
            with open(prop_file, 'w') as f:
                f.write(self.properties_editor.toPlainText())
            self.statusBar().showMessage("Properties saved successfully", 3000)
        except Exception as e:
            self.show_error(f"Failed to save properties: {str(e)}")

    def safe_mod_search(self):
        """Safe mod search with error handling"""
        platform = self.mods_platform_combo.currentText()
        query = self.mod_search.text()
        if not query:
            return
        self.progress.show()
        self.statusBar().showMessage("Searching mods...")
        try:
            if platform == "Modrinth":
                server_version = self.servers[self.current_server]['mc_version'] if self.current_server else None
                self.search_thread = ModSearchThread(
                    query, 
                    server_version, 
                    self.loader_combo.currentText()
                )
                self.search_thread.finished.connect(self.show_mods)
            elif platform == "CurseForge":
                if not self.api_keys.get('curseforge'):
                    self.show_error("CurseForge API key required!")
                    return
                self.search_thread = CurseForgeSearchThread(
                    query, 
                    6, 
                    self.api_keys['curseforge']
                )
                self.search_thread.finished.connect(
                    lambda data: self.show_mods(self._format_curseforge_results(data)))
            self.search_thread.error.connect(self.show_search_error)
            self.search_thread.start()
        except Exception as e:
            self.progress.hide()
            self.show_error(f"Search failed: {str(e)}")
            
    def safe_plugin_search(self):
        """Safe plugin search with error handling"""
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
                self.search_thread = CurseForgeSearchThread(
                    query, 
                    5, 
                    self.api_keys['curseforge']
                )
                self.search_thread.finished.connect(
                    lambda data: self.show_plugins(self._format_curseforge_results(data)))
            elif platform == "Modrinth":
                server_version = self.servers[self.current_server]['mc_version'] if self.current_server else None
                self.search_thread = ModSearchThread(
                    query, 
                    server_version, 
                    "bukkit"
                )
                self.search_thread.finished.connect(self.show_plugins)
            
            self.search_thread.error.connect(self.show_search_error)
            self.search_thread.start()
        except Exception as e:
            self.progress.hide()
            self.show_error(f"Search failed: {str(e)}")

    def _format_curseforge_results(self, results):
        """Format CurseForge API results for display"""
        return [{
            'id': res['id'],
            'name': res['name'],
            'description': res.get('summary', 'No description'),
            'icon_url': res['logo']['url'] if res.get('logo') else None,
            'versions': res['latestFiles']
        } for res in results]

    def show_mods(self, mods):
        """Display mod search results"""
        self.clear_layout(self.mod_list_layout)
        for mod in mods:
            self.create_resource_card(mod, self.mod_list_layout, self.install_mod)
        self.mod_list_layout.addStretch()
        self.progress.hide()
        self.statusBar().showMessage(f"Found {len(mods)} mods", 3000)

    def show_plugins(self, plugins):
        """Display plugin search results"""
        self.clear_layout(self.plugin_list_layout)
        for plugin in plugins:
            self.create_resource_card(plugin, self.plugin_list_layout, self.install_plugin)
        self.plugin_list_layout.addStretch()
        self.progress.hide()
        self.statusBar().showMessage(f"Found {len(plugins)} plugins", 3000)

    def create_resource_card(self, data, layout, install_handler):
        """Create resource card for mod/plugin display"""
        widget = QWidget()
        widget.setFixedHeight(100)
        
        hbox = QHBoxLayout(widget)
        icon = QLabel()
        icon.setFixedSize(80, 80)
        
        text = QVBoxLayout()
        title = QLabel(f"<b>{data.get('title', data.get('name'))}</b>")
        desc = QLabel(data.get('description', 'No description')[:200] + "...")
        text.addWidget(title)
        text.addWidget(desc)
        
        btn = QPushButton("Install")
        btn.clicked.connect(lambda _, d=data: install_handler(d))
        
        hbox.addWidget(icon)
        hbox.addLayout(text)
        hbox.addWidget(btn)
        layout.addWidget(widget)
        
        if data.get('icon_url'):
            self.load_item_icon(data.get('project_id', data.get('id')), data['icon_url'], icon)

    def load_item_icon(self, item_id, url, target_label):
        """Load item icon in background thread"""
        loader = ImageLoaderThread(url, item_id)
        loader.loaded.connect(lambda i, p: self.update_icon(target_label, p))
        loader.finished.connect(lambda: self.current_image_loaders.remove(loader))
        self.current_image_loaders.append(loader)
        loader.start()

    def update_icon(self, label, pixmap):
        """Update icon display"""
        label.setPixmap(pixmap.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def install_mod(self, mod):
        """Install selected mod"""
        self.install_resource(mod, "mods")

    def install_plugin(self, plugin):
        """Install selected plugin"""
        self.install_resource(plugin, "plugins")

    def install_resource(self, resource, target_type):
        """Install resource with version selection"""
        if not self.current_server:
            self.show_error("Select a server first!")
            return
        
        try:
            versions = resource.get('versions', [])
            if not versions:
                self.show_error("No installable versions found")
                return
                
            dialog = VersionSelectDialog(versions, self)
            if dialog.exec() != QDialog.Accepted:
                return
                
            version = dialog.selected_version()
            server_path = self.servers[self.current_server]['path']
            target_dir = os.path.join(server_path, target_type)
            os.makedirs(target_dir, exist_ok=True)
            
            if self.mods_platform_combo.currentText() == "Modrinth":
                file = version['files'][0]
                url = file['url']
                filename = file['filename']
            else:
                file = version
                url = file['downloadUrl']
                filename = file['fileName']
            
            # Sanitize filename
            filename = re.sub(r'[^\w\-\.]', '_', filename)
            dest_path = os.path.join(target_dir, filename)
            
            if os.path.exists(dest_path):
                reply = QMessageBox.question(
                    self, "File Exists", 
                    f"{filename} already exists. Overwrite?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    return
            
            downloader = FileDownloader()
            downloader.download_file(url, dest_path)
            self.show_info(f"Installed {filename}")
        except Exception as e:
            self.show_error(f"Install failed: {str(e)}")

    def clear_layout(self, layout):
        """Clear a layout of all widgets"""
        while layout.count():
            item = layout.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()

    def start_url_install(self, target_type):
        """Start URL-based install process"""
        url_input = getattr(self, f"{target_type}_url_input")
        urls = [url.strip() for url in url_input.toPlainText().split('\n') if url.strip()]
        
        if not urls:
            self.show_error("No URLs provided!")
            return
            
        if not self.current_server:
            self.show_error("Select a server first!")
            return
            
        server_path = self.servers[self.current_server]['path']
        api_key = self.api_keys.get('curseforge', '')
        
        progress_bar = getattr(self, f"{target_type}_progress")
        status_label = getattr(self, f"{target_type}_status")
        
        self.install_thread = UrlInstallThread(urls, server_path, api_key, target_type)
        self.install_thread.progress.connect(
            lambda val, text: (progress_bar.setValue(val), status_label.setText(text))
        )
        self.install_thread.finished.connect(lambda: status_label.setText("Installation completed"))
        self.install_thread.error.connect(lambda err: status_label.setText(f"Error: {err}"))
        
        progress_bar.setValue(0)
        status_label.setText("Starting installation...")
        self.install_thread.start()

    def cancel_url_install(self):
        """Cancel URL install process"""
        if hasattr(self, 'install_thread') and self.install_thread.isRunning():
            self.install_thread.cancel()
            status_label = getattr(self, f"{self.install_thread.target_type}_status")
            status_label.setText("Installation cancelled")
            progress_bar = getattr(self, f"{self.install_thread.target_type}_progress")
            progress_bar.setValue(0)

    def check_mod_updates(self):
        """Check for mod updates (stub implementation)"""
        if not self.current_server:
            return
            
        self.show_info("Mod update check will be implemented in the next version")

    def closeEvent(self, event):
        """Handle application close event"""
        self.stop_all_threads()
        self.status_timer.stop()
        self.save_profiles()
        self.save_api_keys()
        event.accept()
        
    def stop_all_threads(self):
        """Stop all background threads"""
        if DEBUG_MODE:
            print("[DEBUG] Stopping all threads...")
            
        # Stop server threads
        for server_name, server in self.servers.items():
            if server.get('thread') and server['thread'].isRunning():
                if DEBUG_MODE:
                    print(f"[DEBUG] Stopping server: {server_name}")
                server['thread'].stop()
                server['thread'].wait(5000)
        
        # Stop image loaders
        for loader in self.current_image_loaders[:]:
            if loader.isRunning():
                if DEBUG_MODE:
                    print(f"[DEBUG] Stopping image loader")
                loader.quit()
                loader.wait(1000)
        
        # Stop search thread
        if hasattr(self, 'search_thread') and self.search_thread.isRunning():
            if DEBUG_MODE:
                print("[DEBUG] Stopping search thread")
            self.search_thread.quit()
            self.search_thread.wait(1000)
            
        # Stop install thread
        if hasattr(self, 'install_thread') and self.install_thread.isRunning():
            if DEBUG_MODE:
                print("[DEBUG] Stopping install thread")
            self.install_thread.cancel()
            self.install_thread.quit()
            self.install_thread.wait(1000)
            
        if DEBUG_MODE:
            print("[DEBUG] All threads stopped")

# =============================================================================
# APPLICATION ENTRY POINT
# =============================================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    qdarktheme.setup_theme()
    window = ServerManager()
    window.show()
    sys.exit(app.exec())