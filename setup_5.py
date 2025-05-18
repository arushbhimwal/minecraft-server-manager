#!/usr/bin/env python3
import sys
import re
import os
import platform
import json
import subprocess
import requests
import logging
from datetime import datetime
from mcrcon import MCRcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QTabWidget, QTextEdit,
    QLineEdit, QFileDialog, QMessageBox, QTreeView, QFileSystemModel,
    QInputDialog, QListWidget, QListWidgetItem, QProgressBar,
    QScrollArea, QSizePolicy, QFormLayout, QDialog, QDialogButtonBox
)
from PySide6.QtGui import QIcon, QAction, QPixmap, QImage, QPainter, QColor
from PySide6.QtCore import Qt, QThread, Signal, QDir, QTimer, QSize, QStandardPaths
import qdarktheme

# Logging configuration
logging.basicConfig(
    filename='server_manager.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class ApiKeyDialog(QDialog):
    """Dialog for API key configuration"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("API Key Configuration")
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout(self)
        
        self.curseforge_key = QLineEdit()
        self.curseforge_key.setPlaceholderText("Enter CurseForge API key...")
        self.modrinth_key = QLineEdit()
        self.modrinth_key.setPlaceholderText("Enter Modrinth API key...")
        
        form = QFormLayout()
        form.addRow("CurseForge API Key:", self.curseforge_key)
        form.addRow("Modrinth API Key:", self.modrinth_key)
        layout.addLayout(form)
        
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

class ServerThread(QThread):
    """Thread for managing server process"""
    output = Signal(str)
    stopped = Signal()

    def __init__(self, command, cwd):
        super().__init__()
        self.command = command
        self.cwd = cwd
        self.process = None
        self.running = False

    def run(self):
        """Main execution method"""
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
        """Stop the server process"""
        self.running = False
        if self.process:
            self.process.terminate()

class ModSearchThread(QThread):
    """Thread for searching Modrinth"""
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, query, mc_version=None, api_key=None, content_type="mods"):
        super().__init__()
        self.query = query
        self.mc_version = mc_version
        self.api_key = api_key
        self.content_type = content_type

    def run(self):
        """Execute search"""
        try:
            facets = []
            if self.content_type == "mods":
                facets.append(["categories:forge"])
            elif self.content_type == "plugins":
                facets.append(["categories:bukkit"])
            
            # Add Minecraft version filter if available
            if self.mc_version:
                facets.append([f"versions:{self.mc_version}"])
            
            headers = {}
            if self.api_key:
                headers["Authorization"] = self.api_key
                
            response = requests.get(
                f"https://api.modrinth.com/v2/search?query={self.query}&facets={json.dumps(facets)}",
                headers=headers,
                timeout=10
            )
            response.raise_for_status()
            self.finished.emit(response.json()['hits'])
        except Exception as e:
            self.error.emit(str(e))
            logger.error(f"Mod search error: {str(e)}")

class CurseForgeSearchThread(QThread):
    """Thread for searching CurseForge"""
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, query, class_id, api_key):
        super().__init__()
        self.query = query
        self.class_id = class_id
        self.api_key = api_key

    def run(self):
        """Execute search"""
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
            logger.error(f"CurseForge search error: {str(e)}")

class ImageLoaderThread(QThread):
    """Thread for loading images"""
    loaded = Signal(str, QPixmap)

    def __init__(self, url, item_id):
        super().__init__()
        self.url = url
        self.item_id = item_id

    def run(self):
        """Load image from URL"""
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

    def __init__(self, urls, server_path, api_keys, target_folder):
        super().__init__()
        self.urls = urls
        self.server_path = server_path
        self.api_keys = api_keys
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
        # Direct file URL
        if url.endswith(('.jar', '.zip')):
            return self.direct_download(url)

        # Modrinth mod
        if 'modrinth.com' in url:
            return self.handle_modrinth(url)

        # CurseForge mod/plugin
        if 'curseforge.com' in url:
            return self.handle_curseforge(url)

        raise ValueError("Unsupported URL type")

    def direct_download(self, url):
        save_path = os.path.join(self.server_path, self.target_folder, os.path.basename(url.split('?')[0]))
        return self.download_file(url, save_path)

    def get_save_path(self, url):
        filename = os.path.basename(url.split('?')[0])
        if 'plugin' in filename.lower():
            folder = 'plugins'
        else:
            folder = 'mods'
        save_dir = os.path.join(self.server_path, folder)
        os.makedirs(save_dir, exist_ok=True)
        return os.path.join(save_dir, filename)

    def handle_modrinth(self, url):
        match = re.search(r'modrinth\.com/(\w+)/([\w-]+)', url)
        if not match:
            raise ValueError("Invalid Modrinth URL")
        
        project_type = match.group(1)
        project_id = match.group(2)
        
        headers = {}
        if self.api_keys.get('modrinth'):
            headers['Authorization'] = self.api_keys['modrinth']
        
        # Get latest version
        response = requests.get(
            f'https://api.modrinth.com/v2/project/{project_id}/version',
            headers=headers
        )
        versions = response.json()
        if not versions:
            raise ValueError("No versions available")
        
        # Get server version from installation
        server_version = self.get_server_version()
        
        # Find compatible version
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

        headers = {'x-api-key': self.api_keys['curseforge']}
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

    def get_server_version(self):
        # Extract server version from server.properties
        props_path = os.path.join(self.server_path, 'server.properties')
        if os.path.exists(props_path):
            with open(props_path, 'r') as f:
                for line in f:
                    if line.startswith('level-type='):
                        return line.split('=')[1].strip()
        return None

class ServerManager(QMainWindow):
    """Main application window"""
    def __init__(self):
        super().__init__()
        self.java_versions = ['8', '11', '17', '21']
        self.loaders = ['Vanilla', 'Paper', 'Spigot', 'Forge', 'Fabric', 'Quilt', 'Mohist']
        self.servers = {}
        self.current_server = None
        self.profiles_file = "profiles.json"
        self.settings_file = "settings.json"
        self.current_image_loaders = []
        self.api_keys = {'curseforge': '', 'modrinth': ''}

        # Initialize UI
        self.setWindowTitle("Minecraft Server Manager")
        self.setGeometry(100, 100, 1200, 800)
        self.setup_ui()
        
        # Load data
        self.load_profiles()
        self.check_java()
        self.check_api_keys()
        
        logger.info("Application started")

    def setup_ui(self):
        """Initialize user interface"""
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
        
        # Server controls
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

        # Right panel - Main content
        content_panel = QWidget()
        content_layout = QVBoxLayout(content_panel)
        
        # Server creation controls
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
        content_layout.addWidget(self.tabs)

        # Console tab
        console_tab = QWidget()
        console_layout = QVBoxLayout(console_tab)
        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        console_layout.addWidget(self.console_output)
        self.command_input = QLineEdit()
        self.command_input.returnPressed.connect(self.send_command)
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
        file_layout.addWidget(self.file_view)
        self.tabs.addTab(file_tab, "Files")

        # Mods tab
        mods_tab = QWidget()
        mods_layout = QVBoxLayout(mods_tab)
        mods_layout.insertLayout(0, self.create_url_section("mods"))

        # Mods platform selection
        mods_platform_layout = QHBoxLayout()
        self.mods_platform_combo = QComboBox()
        self.mods_platform_combo.addItems(["Modrinth", "CurseForge"])
        mods_platform_layout.addWidget(QLabel("Source:"))
        mods_platform_layout.addWidget(self.mods_platform_combo)
        mods_layout.addLayout(mods_platform_layout)
        
        # Mods search
        mods_search_layout = QHBoxLayout()
        self.mod_search = QLineEdit()
        self.mod_search.setPlaceholderText("Search mods...")
        btn_mod_search = QPushButton("Search")
        btn_mod_search.clicked.connect(self.safe_mod_search)
        mods_search_layout.addWidget(self.mod_search)
        mods_search_layout.addWidget(btn_mod_search)
        mods_layout.addLayout(mods_search_layout)
        
        # Mods list
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
        plugins_layout.insertLayout(0, self.create_url_section("plugins"))

        # Plugins platform selection
        plugins_platform_layout = QHBoxLayout()
        self.plugins_platform_combo = QComboBox()
        self.plugins_platform_combo.addItems(["CurseForge", "Modrinth"])
        plugins_platform_layout.addWidget(QLabel("Source:"))
        plugins_platform_layout.addWidget(self.plugins_platform_combo)
        plugins_layout.addLayout(plugins_platform_layout)
        
        # Plugins search
        plugins_search_layout = QHBoxLayout()
        self.plugin_search = QLineEdit()
        self.plugin_search.setPlaceholderText("Search plugins...")
        btn_plugin_search = QPushButton("Search")
        btn_plugin_search.clicked.connect(self.safe_plugin_search)
        plugins_search_layout.addWidget(self.plugin_search)
        plugins_search_layout.addWidget(btn_plugin_search)
        plugins_layout.addLayout(plugins_search_layout)
        
        # Plugins list
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
        self.modrinth_key_input = QLineEdit()
        self.modrinth_key_input.setPlaceholderText("Enter Modrinth API key...")
        settings_layout.addRow("Modrinth API Key:", self.modrinth_key_input)
        btn_save = QPushButton("Save API Keys")
        btn_save.clicked.connect(self.save_api_keys)
        settings_layout.addRow(btn_save)
        self.tabs.addTab(settings_tab, "Settings")

        main_layout.addWidget(content_panel, stretch=3)
        self.update_controls()

    def create_url_section(self, target_type):
        url_layout = QVBoxLayout()
        url_layout.setContentsMargins(0, 5, 0, 5)  # Reduced vertical margins
        url_layout.setSpacing(5)  # Reduced spacing between widgets

        lbl = QLabel(f"Install {target_type.capitalize()} from URLs:")
        lbl.setStyleSheet("font-weight: bold; margin-bottom: 2px;")
        url_layout.addWidget(lbl)

        url_input = QTextEdit()
        url_input.setPlaceholderText(f"Paste {target_type} URLs (one per line)...")
        url_input.setMaximumHeight(60)  # Reduced from 100
        url_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        url_layout.addWidget(url_input)

        progress = QProgressBar()
        progress.setFixedHeight(20)  # Compact progress bar
        progress.hide()
        url_layout.addWidget(progress)

        status = QLabel()
        status.setFixedHeight(18)  # Compact status label
        status.hide()
        url_layout.addWidget(status)

        btn_install = QPushButton(f"Install {target_type.capitalize()}")
        btn_install.setFixedHeight(30)  # Compact button
        btn_install.clicked.connect(lambda: self.start_url_install(target_type))
        url_layout.addWidget(btn_install)

        # Store references
        setattr(self, f"{target_type}_url_input", url_input)
        setattr(self, f"{target_type}_progress", progress)
        setattr(self, f"{target_type}_status", status)
    
        return url_layout

    def start_url_install(self, target_type):
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
            self.api_keys,
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
        getattr(self, f"{target_type}_progress").setValue(value)
        getattr(self, f"{target_type}_status").setText(message)

    def url_install_finished(self, target_type):
        getattr(self, f"{target_type}_progress").hide()
        getattr(self, f"{target_type}_status").hide()
        QMessageBox.information(self, "Success", f"{target_type.capitalize()} installed successfully!")

    def url_install_error(self, message, target_type):
        getattr(self, f"{target_type}_progress").hide()
        getattr(self, f"{target_type}_status").hide()
        QMessageBox.critical(self, "Error", message)

    def check_api_keys(self):
        """Check and load API keys"""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    self.api_keys = json.load(f)
                    self.curseforge_key_input.setText(self.api_keys.get('curseforge', ''))
                    self.modrinth_key_input.setText(self.api_keys.get('modrinth', ''))
                    
            if not self.api_keys.get('curseforge') or not self.api_keys.get('modrinth'):
                self.show_api_key_dialog()
                
        except Exception as e:
            logger.error(f"API key check failed: {str(e)}")
            self.show_api_key_dialog()

    def show_api_key_dialog(self):
        """Show API key configuration dialog"""
        dialog = ApiKeyDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.api_keys['curseforge'] = dialog.curseforge_key.text()
            self.api_keys['modrinth'] = dialog.modrinth_key.text()
            self.curseforge_key_input.setText(self.api_keys['curseforge'])
            self.modrinth_key_input.setText(self.api_keys['modrinth'])
            self.save_api_keys()
        else:
            QMessageBox.warning(
                self,
                "API Keys Required",
                "You must configure API keys to use all features."
            )
            self.show_api_key_dialog()

    def save_api_keys(self):
        """Save API keys to file"""
        try:
            self.api_keys['curseforge'] = self.curseforge_key_input.text()
            self.api_keys['modrinth'] = self.modrinth_key_input.text()
            
            with open(self.settings_file, 'w') as f:
                json.dump(self.api_keys, f)
                
            QMessageBox.information(self, "Success", "API keys saved!")
            logger.info("API keys updated")
        except Exception as e:
            logger.error(f"API key save failed: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to save API keys: {str(e)}")

    def closeEvent(self, event):
        """Handle application shutdown"""
        for loader in self.current_image_loaders:
            if loader.isRunning():
                loader.quit()
        self.save_profiles()
        self.save_api_keys()
        logger.info("Application closed")
        event.accept()

    def select_server_directory(self):
        """Select server directory"""
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
        """Configure new server"""
        server_dir = os.path.join(self.server_path.text(), name)
        os.makedirs(server_dir, exist_ok=True)
        logger.info(f"Creating server: {name} at {server_dir}")

        try:
            loader = self.loader_combo.currentText()
            version = self.version_combo.currentText()
            
            self.servers[name] = {
                "path": server_dir,
                "status": "stopped",
                "max_ram": "2G",
                "mc_version": version,
                "thread": None
            }

            if loader == "Vanilla":
                url = self.get_vanilla_url(version)
            elif loader == "Paper":
                url = self.get_paper_url(version)
            else:
                raise ValueError("Unsupported loader")

            self.download_file(url, os.path.join(server_dir, "server.jar"))
            self.create_server_properties(server_dir)
            
            with open(os.path.join(server_dir, "eula.txt"), 'w') as f:
                f.write("eula=true\n")
            
            self.save_profiles()
            self.update_server_list()
            logger.info(f"Server {name} created successfully")
            QMessageBox.information(self, "Success", f"Server '{name}' created!")

        except Exception as e:
            logger.error(f"Server creation failed: {str(e)}")
            QMessageBox.critical(self, "Error", f"Server creation failed: {str(e)}")
            if os.path.exists(server_dir):
                try:
                    if platform.system() == "Windows":
                        subprocess.run(['cmd', '/c', 'rmdir', '/s', '/q', server_dir], check=True)
                    else:
                        subprocess.run(["rm", "-rf", server_dir], check=True)
                except Exception as cleanup_error:
                    logger.error(f"Cleanup failed: {str(cleanup_error)}")

    def update_versions(self, loader_name):
        """Update available Minecraft versions"""
        self.version_combo.clear()
        self.progress.show()
        logger.info(f"Fetching versions for {loader_name}")
        
        try:
            if loader_name == "Vanilla":
                response = requests.get("https://piston-meta.mojang.com/mc/game/version_manifest.json", timeout=10)
                data = response.json()
                versions = [v['id'] for v in data['versions'] if v['type'] == 'release']
            elif loader_name == "Paper":
                response = requests.get("https://api.papermc.io/v2/projects/paper", timeout=10)
                data = response.json()
                versions = data['versions'][::-1]
            elif loader_name == "Fabric":
                response = requests.get("https://meta.fabricmc.net/v2/versions/game", timeout=10)
                versions = [v['version'] for v in response.json()]
            else:
                versions = ["Version selection not implemented"]
            
            self.version_combo.addItems(versions[:20])
            logger.debug(f"Loaded {len(versions)} versions for {loader_name}")
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to fetch versions: {str(e)}")
            logger.error(f"Version fetch error: {str(e)}")
        finally:
            self.progress.hide()

    def get_vanilla_url(self, version):
        """Get Vanilla server download URL"""
        manifest = requests.get("https://piston-meta.mojang.com/mc/game/version_manifest.json", timeout=10).json()
        for v in manifest['versions']:
            if v['id'] == version and v['type'] == "release":
                version_data = requests.get(v['url'], timeout=10).json()
                return version_data['downloads']['server']['url']
        raise ValueError("Version not found")

    def get_paper_url(self, version):
        """Get PaperMC server download URL"""
        builds = requests.get(f"https://api.papermc.io/v2/projects/paper/versions/{version}", timeout=10).json()
        if not builds['builds']:
            raise ValueError("No builds found")
        latest = builds['builds'][-1]
        return f"https://api.papermc.io/v2/projects/paper/versions/{version}/builds/{latest}/downloads/paper-{version}-{latest}.jar"

    def download_file(self, url, path):
        """Download a file from URL"""
        try:
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()
            with open(path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info(f"Downloaded {os.path.basename(path)}")
        except Exception as e:
            logger.error(f"Download failed: {str(e)}")
            raise Exception(f"Download failed: {str(e)}")

    def create_server_properties(self, server_dir):
        """Create server.properties file"""
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
            logger.info("Created server.properties")
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
            logger.warning("Using fallback password generation")
            return str(os.urandom(12).hex())

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
                logger.info(f"Opened file: {path}")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not open file: {str(e)}")
                logger.error(f"File open error: {str(e)}")

    def load_profiles(self):
        """Load server profiles"""
        try:
            if os.path.exists(self.profiles_file):
                with open(self.profiles_file, 'r') as f:
                    loaded_servers = json.load(f)
                    self.servers = {name: {**data, 'thread': None} 
                                   for name, data in loaded_servers.items()}
                    self.update_server_list()
                    logger.info("Loaded server profiles")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load profiles: {str(e)}")
            logger.error(f"Profile load error: {str(e)}")

    def save_profiles(self):
        """Save server profiles"""
        try:
            save_data = {
                name: {k: v for k, v in data.items() if k != 'thread'} 
                for name, data in self.servers.items()
            }
            with open(self.profiles_file, 'w') as f:
                json.dump(save_data, f, indent=2)
            logger.info("Saved server profiles")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save profiles: {str(e)}")
            logger.error(f"Profile save error: {str(e)}")

    def update_server_list(self):
        """Update server list display"""
        self.server_list.clear()
        for server_name in self.servers:
            item = QListWidgetItem(server_name)
            status = self.servers[server_name].get('status', 'stopped')
            item.setForeground(Qt.green if status == 'running' else Qt.red)
            self.server_list.addItem(item)
        logger.debug("Updated server list")

    def select_server(self, item):
        """Handle server selection"""
        self.current_server = item.text()
        server_data = self.servers[self.current_server]
        self.file_model.setRootPath(server_data['path'])
        self.file_view.setRootIndex(self.file_model.index(server_data['path']))
        self.update_controls()
        logger.info(f"Selected server: {self.current_server}")

    def update_controls(self):
        """Update UI control states"""
        has_selection = self.current_server is not None
        self.btn_start.setEnabled(has_selection)
        self.btn_stop.setEnabled(has_selection)
        self.btn_delete.setEnabled(has_selection)
        
        if has_selection:
            status = self.servers[self.current_server].get('status', 'stopped')
            self.btn_start.setEnabled(status == 'stopped')
            self.btn_stop.setEnabled(status == 'running')
        logger.debug("Updated UI controls")

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
                logger.info(f"Detected Java version: {detected_version}")
        except (subprocess.CalledProcessError, FileNotFoundError):
            QMessageBox.warning(
                self,
                "Java Not Found",
                "Java runtime not detected! Please install Java SE."
            )
            logger.warning("Java runtime not found")

    def send_command(self):
        """Send command to server"""
        cmd = self.command_input.text()
        self.command_input.clear()
        if not self.current_server:
            return
            
        try:
            server = self.servers[self.current_server]
            with MCRcon("localhost", "password", 25575) as mcr:
                response = mcr.command(cmd)
                self.console_output.append(f"> {cmd}\n{response}")
                logger.info(f"Executed command: {cmd}")
        except Exception as e:
            error_msg = f"RCON error: {str(e)}"
            self.console_output.append(error_msg)
            logger.error(error_msg)

    def start_server(self):
        """Start selected server"""
        if not self.current_server:
            return
            
        server = self.servers[self.current_server]
        if server['status'] == 'stopped':
            try:
                java_path = "java"
                if platform.system() == "Windows":
                    java_path += ".exe"
                
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
                self.update_controls()
                logger.info(f"Started server: {self.current_server}")
                
            except Exception as e:
                logger.error(f"Server start failed: {str(e)}")
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
                self.update_controls()
                logger.info(f"Stopping server: {self.current_server}")
            except Exception as e:
                logger.error(f"Server stop failed: {str(e)}")
                QMessageBox.critical(self, "Error", f"Failed to stop server: {str(e)}")

    def handle_server_output(self, message):
        """Handle server console output"""
        self.console_output.append(message)
        logger.debug(f"Server output: {message}")

    def handle_server_stop(self):
        """Handle server shutdown"""
        if self.current_server:
            self.servers[self.current_server]['status'] = 'stopped'
            self.servers[self.current_server]['thread'] = None
            self.save_profiles()
            self.update_server_list()
            self.update_controls()
            logger.info(f"Server stopped: {self.current_server}")

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
                self.update_controls()
                logger.info(f"Deleted server: {server_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Delete failed: {str(e)}")
                logger.error(f"Server deletion error: {str(e)}")

    def safe_mod_search(self):
        """Search for mods from selected platform"""
        platform = self.mods_platform_combo.currentText()
        query = self.mod_search.text()
        
        if not query:
            return
            
        self.progress.show()
        
        try:
            if platform == "Modrinth":
                if not self.api_keys.get('modrinth'):
                    QMessageBox.warning(self, "Error", "Modrinth API key required!")
                    self.show_api_key_dialog()
                    return
                
                server_version = self.servers[self.current_server]['mc_version'] if self.current_server else None
                self.search_thread = ModSearchThread(
                    query, 
                    mc_version=server_version,
                    api_key=self.api_keys['modrinth'],
                    content_type="mods"
                )
                self.search_thread.finished.connect(self.show_mods)
                self.search_thread.error.connect(self.show_search_error)
                self.search_thread.start()
                
            elif platform == "CurseForge":
                if not self.api_keys.get('curseforge'):
                    QMessageBox.warning(self, "Error", "CurseForge API key required!")
                    self.show_api_key_dialog()
                    return
                
                self.search_thread = CurseForgeSearchThread(
                    query,
                    class_id=6,  # 6 = Mods in CurseForge
                    api_key=self.api_keys['curseforge']
                )
                self.search_thread.finished.connect(lambda data: self.show_mods(self._format_cf_mods(data)))
                self.search_thread.error.connect(self.show_search_error)
                self.search_thread.start()
                
        except Exception as e:
            self.progress.hide()
            QMessageBox.critical(self, "Error", f"Search failed: {str(e)}")

    def safe_plugin_search(self):
        """Search for plugins from selected platform"""
        platform = self.plugins_platform_combo.currentText()
        query = self.plugin_search.text()
        
        if not query:
            return
            
        self.progress.show()
        
        try:
            if platform == "CurseForge":
                if not self.api_keys.get('curseforge'):
                    QMessageBox.warning(self, "Error", "CurseForge API key required!")
                    self.show_api_key_dialog()
                    return
                
                self.search_thread = CurseForgeSearchThread(
                    query,
                    class_id=5,  # 5 = Plugins in CurseForge
                    api_key=self.api_keys['curseforge']
                )
                self.search_thread.finished.connect(lambda data: self.show_plugins(self._format_cf_plugins(data)))
                self.search_thread.error.connect(self.show_search_error)
                self.search_thread.start()
                
            elif platform == "Modrinth":
                if not self.api_keys.get('modrinth'):
                    QMessageBox.warning(self, "Error", "Modrinth API key required!")
                    self.show_api_key_dialog()
                    return
                
                server_version = self.servers[self.current_server]['mc_version'] if self.current_server else None
                self.search_thread = ModSearchThread(
                    query, 
                    mc_version=server_version,
                    api_key=self.api_keys['modrinth'],
                    content_type="plugins"
                )
                self.search_thread.finished.connect(lambda data: self.show_plugins(self._format_modrinth_plugins(data)))
                self.search_thread.error.connect(self.show_search_error)
                self.search_thread.start()
                
        except Exception as e:
            self.progress.hide()
            QMessageBox.critical(self, "Error", f"Search failed: {str(e)}")

    def _format_cf_mods(self, cf_mods):
        """Format CurseForge mods for display"""
        return [{
            'id': mod['id'],
            'title': mod['name'],
            'description': mod.get('summary', 'No description'),
            'icon_url': mod['logo']['url'] if mod.get('logo') else None,
            'versions': mod['latestFiles']
        } for mod in cf_mods]

    def _format_modrinth_plugins(self, modrinth_plugins):
        """Format Modrinth plugins for display"""
        return [{
            'id': plugin['project_id'],
            'name': plugin['title'],
            'description': plugin.get('description', 'No description'),
            'icon_url': plugin.get('icon_url'),
            'versions': plugin['versions']
        } for plugin in modrinth_plugins]

    def _format_cf_plugins(self, cf_plugins):
        """Format CurseForge plugins for display"""
        return [{
            'id': plugin['id'],
            'name': plugin['name'],
            'description': plugin.get('summary', 'No description'),
            'icon_url': plugin['logo']['url'] if plugin.get('logo') else None,
            'versions': plugin['latestFiles']
        } for plugin in cf_plugins]

    def show_mods(self, mods):
        """Display mods in the mods tab"""
        self.progress.hide()
        while self.mod_list_layout.count():
            item = self.mod_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        try:
            for mod in mods:
                self.create_mod_card(mod)
            self.mod_list_layout.addStretch()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to display mods: {str(e)}")

    def show_plugins(self, plugins):
        """Display plugins in the plugins tab"""
        self.progress.hide()
        while self.plugin_list_layout.count():
            item = self.plugin_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        try:
            for plugin in plugins:
                self.create_plugin_card(plugin)
            self.plugin_list_layout.addStretch()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to display plugins: {str(e)}")

    def create_mod_card(self, mod):
        """Create a mod card UI element"""
        widget = QWidget()
        widget.setProperty("mod_id", mod['id'])
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
        desc.setWordWrap(True)
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
        """Create a plugin card UI element"""
        widget = QWidget()
        widget.setProperty("plugin_id", plugin['id'])
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
        desc.setWordWrap(True)
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
        """Load and display item icon"""
        loader = ImageLoaderThread(url, item_id)
        loader.loaded.connect(lambda item_id, pixmap: self.update_item_icon(item_id, pixmap, target_label))
        self.current_image_loaders.append(loader)
        loader.start()

    def update_item_icon(self, item_id, pixmap, target_label):
        """Update the icon for a specific item"""
        target_label.setPixmap(pixmap.scaled(
            80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))

    def install_mod(self, mod):
        """Install selected mod"""
        platform = self.mods_platform_combo.currentText()
        if not self.current_server:
            QMessageBox.warning(self, "Error", "Select a server first!")
            return

        server_path = self.servers[self.current_server]['path']
        mods_dir = os.path.join(server_path, "mods")
        os.makedirs(mods_dir, exist_ok=True)

        try:
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
        platform = self.plugins_platform_combo.currentText()
        if not self.current_server:
            QMessageBox.warning(self, "Error", "Select a server first!")
            return

        server_path = self.servers[self.current_server]['path']
        plugins_dir = os.path.join(server_path, "plugins")
        os.makedirs(plugins_dir, exist_ok=True)

        try:
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

if __name__ == "__main__":
    app = QApplication(sys.argv)
    qdarktheme.setup_theme()
    window = ServerManager()
    window.show()
    sys.exit(app.exec())