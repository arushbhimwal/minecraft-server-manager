#!/usr/bin/env python3
import sys
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
    QScrollArea, QSizePolicy, QFormLayout
)
from PySide6.QtGui import QIcon, QAction, QPixmap, QImage, QPainter, QColor
from PySide6.QtCore import Qt, QThread, Signal, QDir, QTimer, QSize, QStandardPaths
import qdarktheme

# Initialize logging
logging.basicConfig(
    filename='server_manager.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

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

    def __init__(self, query, mc_version=None):
        super().__init__()
        self.query = query
        self.mc_version = mc_version

    def run(self):
        try:
            facets = [["categories:forge"]]
            if self.mc_version:
                facets.append([f"versions:{self.mc_version}"])
            
            response = requests.get(
                f"https://api.modrinth.com/v2/search?query={self.query}&facets={json.dumps(facets)}",
                timeout=10
            )
            response.raise_for_status()
            self.finished.emit(response.json()['hits'])
        except Exception as e:
            self.error.emit(str(e))
            logger.error(f"Mod search error: {str(e)}")

class ImageLoaderThread(QThread):
    loaded = Signal(str, QPixmap)

    def __init__(self, url, mod_id):
        super().__init__()
        self.url = url
        self.mod_id = mod_id

    def run(self):
        try:
            response = requests.get(self.url, timeout=10)
            if response.status_code == 200:
                image = QImage()
                image.loadFromData(response.content)
                if not image.isNull():
                    pixmap = QPixmap.fromImage(image)
                    self.loaded.emit(self.mod_id, pixmap)
                    return
        except Exception as e:
            logger.warning(f"Image load failed: {str(e)}")
        
        pixmap = QPixmap(80, 80)
        pixmap.fill(QColor(53, 53, 53))
        painter = QPainter(pixmap)
        painter.setPen(Qt.white)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "No Image")
        painter.end()
        self.loaded.emit(self.mod_id, pixmap)

class ServerManager(QMainWindow):
    def __init__(self):
        super().__init__()
        # Initialize settings first
        self.java_versions = ['8', '11', '17', '21']
        self.loaders = ['Vanilla', 'Paper', 'Spigot', 'Forge', 'Fabric', 'Quilt', 'Mohist']
        self.servers = {}
        self.current_server = None
        self.profiles_file = "profiles.json"
        self.settings_file = "settings.json"
        self.current_image_loaders = []
        self.settings = {'curseforge_key': '', 'modrinth_key': ''}

        # Set up UI first
        self.setWindowTitle("Minecraft Server Manager")
        self.setGeometry(100, 100, 1200, 800)
        self.setup_ui()  # Initialize UI components first
        
        # Then load data
        self.load_settings()
        self.load_profiles()
        self.check_java()
        logger.info("Application started")

    def setup_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # Left Panel - Server List
        server_list_panel = QWidget()
        server_list_layout = QVBoxLayout(server_list_panel)
        self.server_list = QListWidget()
        self.server_list.itemClicked.connect(self.select_server)
        server_list_layout.addWidget(QLabel("Servers:"))
        server_list_layout.addWidget(self.server_list)
        
        # Server Controls
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

        # Right Panel - Main Content
        content_panel = QWidget()
        content_layout = QVBoxLayout(content_panel)
        
        # Server Creation Controls
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

        # Progress Bar
        self.progress = QProgressBar()
        self.progress.hide()
        content_layout.addWidget(self.progress)

        # Tabs
        self.tabs = QTabWidget()
        content_layout.addWidget(self.tabs)

        # Console Tab
        console_tab = QWidget()
        console_layout = QVBoxLayout(console_tab)
        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        console_layout.addWidget(self.console_output)
        self.command_input = QLineEdit()
        self.command_input.returnPressed.connect(self.send_command)
        console_layout.addWidget(self.command_input)
        self.tabs.addTab(console_tab, "Console")

        # File Browser Tab
        file_tab = QWidget()
        file_layout = QVBoxLayout(file_tab)
        self.file_model = QFileSystemModel()
        self.file_model.setRootPath(QDir.currentPath())
        self.file_view = QTreeView()
        self.file_view.setModel(self.file_model)
        self.file_view.doubleClicked.connect(self.open_file)
        file_layout.addWidget(self.file_view)
        self.tabs.addTab(file_tab, "Files")

        # Mods Tab
        mods_tab = QWidget()
        mods_layout = QVBoxLayout(mods_tab)
        search_layout = QHBoxLayout()
        self.mod_search = QLineEdit()
        self.mod_search.setPlaceholderText("Search Modrinth...")
        btn_search = QPushButton("Search")
        btn_search.clicked.connect(self.safe_mod_search)
        search_layout.addWidget(self.mod_search)
        search_layout.addWidget(btn_search)
        mods_layout.addLayout(search_layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.mod_list_container = QWidget()
        self.mod_list_layout = QVBoxLayout(self.mod_list_container)
        scroll.setWidget(self.mod_list_container)
        mods_layout.addWidget(scroll)
        self.tabs.addTab(mods_tab, "Mods")

        # Plugins Tab
        plugins_tab = QWidget()
        plugins_layout = QVBoxLayout(plugins_tab)
        search_layout = QHBoxLayout()
        self.plugin_search = QLineEdit()
        self.plugin_search.setPlaceholderText("Search CurseForge...")
        btn_search = QPushButton("Search")
        btn_search.clicked.connect(self.safe_plugin_search)
        search_layout.addWidget(self.plugin_search)
        search_layout.addWidget(btn_search)
        plugins_layout.addLayout(search_layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.plugin_list_container = QWidget()
        self.plugin_list_layout = QVBoxLayout(self.plugin_list_container)
        scroll.setWidget(self.plugin_list_container)
        plugins_layout.addWidget(scroll)
        self.tabs.addTab(plugins_tab, "Plugins")

        # Settings Tab
        settings_tab = QWidget()
        settings_layout = QFormLayout(settings_tab)
        self.curseforge_key = QLineEdit()
        self.curseforge_key.setText(self.settings.get('curseforge_key', ''))
        settings_layout.addRow("CurseForge API Key:", self.curseforge_key)
        self.modrinth_key = QLineEdit()
        self.modrinth_key.setText(self.settings.get('modrinth_key', ''))
        settings_layout.addRow("Modrinth API Key:", self.modrinth_key)
        btn_save = QPushButton("Save Settings")
        btn_save.clicked.connect(self.save_settings)
        settings_layout.addRow(btn_save)
        self.tabs.addTab(settings_tab, "Settings")

        main_layout.addWidget(content_panel, stretch=3)
        self.update_controls()

    def closeEvent(self, event):
        for loader in self.current_image_loaders:
            if loader.isRunning():
                loader.quit()
        self.save_profiles()
        self.save_settings()
        logger.info("Application closed")
        event.accept()

    def select_server_directory(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select Directory", QDir.homePath(), QFileDialog.ShowDirsOnly
        )
        if path:
            self.server_path.setText(path)
            logger.info(f"Selected server directory: {path}")

    def create_new_server(self):
        if not self.server_path.text():
            QMessageBox.warning(self, "Error", "Select server directory first!")
            return

        name, ok = QInputDialog.getText(self, "Server Name", "Enter server name:")
        if ok and name:
            self.setup_server(name)

    def setup_server(self, name):
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
        manifest = requests.get("https://piston-meta.mojang.com/mc/game/version_manifest.json", timeout=10).json()
        for v in manifest['versions']:
            if v['id'] == version and v['type'] == "release":
                version_data = requests.get(v['url'], timeout=10).json()
                return version_data['downloads']['server']['url']
        raise ValueError("Version not found")

    def get_paper_url(self, version):
        builds = requests.get(f"https://api.papermc.io/v2/projects/paper/versions/{version}", timeout=10).json()
        if not builds['builds']:
            raise ValueError("No builds found")
        latest = builds['builds'][-1]
        return f"https://api.papermc.io/v2/projects/paper/versions/{version}/builds/{latest}/downloads/paper-{version}-{latest}.jar"

    def download_file(self, url, path):
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
        try:
            return subprocess.check_output(
                ['openssl', 'rand', '-base64', '12'], 
                universal_newlines=True
            ).strip()
        except Exception:
            logger.warning("Using fallback password generation")
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
                logger.info(f"Opened file: {path}")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not open file: {str(e)}")
                logger.error(f"File open error: {str(e)}")

    def load_profiles(self):
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
        self.server_list.clear()
        for server_name in self.servers:
            item = QListWidgetItem(server_name)
            status = self.servers[server_name].get('status', 'stopped')
            item.setForeground(Qt.green if status == 'running' else Qt.red)
            self.server_list.addItem(item)
        logger.debug("Updated server list")

    def select_server(self, item):
        self.current_server = item.text()
        server_data = self.servers[self.current_server]
        self.file_model.setRootPath(server_data['path'])
        self.file_view.setRootIndex(self.file_model.index(server_data['path']))
        self.update_controls()
        logger.info(f"Selected server: {self.current_server}")

    def update_controls(self):
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
        self.console_output.append(message)
        logger.debug(f"Server output: {message}")

    def handle_server_stop(self):
        if self.current_server:
            self.servers[self.current_server]['status'] = 'stopped'
            self.servers[self.current_server]['thread'] = None
            self.save_profiles()
            self.update_server_list()
            self.update_controls()
            logger.info(f"Server stopped: {self.current_server}")

    def delete_server(self):
        if not self.current_server:
            return
            
        reply = QMessageBox.question(
            self,
            "Delete Server",
            f"Delete '{self.current_server}'?",
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
                self.update_controls()
                logger.info(f"Deleted server: {server_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Delete failed: {str(e)}")
                logger.error(f"Server deletion error: {str(e)}")

    def safe_mod_search(self):
        for loader in self.current_image_loaders:
            if loader.isRunning():
                loader.quit()
        self.current_image_loaders.clear()
        
        while self.mod_list_layout.count():
            item = self.mod_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        query = self.mod_search.text()
        if query:
            self.progress.show()
            server_version = self.servers[self.current_server]['mc_version'] if self.current_server else None
            self.search_thread = ModSearchThread(query, server_version)
            self.search_thread.finished.connect(self.show_mods)
            self.search_thread.error.connect(self.show_mod_error)
            self.search_thread.start()

    def show_mods(self, mods):
        self.progress.hide()
        try:
            for mod in mods:
                self.create_mod_card(mod)
            self.mod_list_layout.addStretch()
            logger.info(f"Displayed {len(mods)} mods")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to display mods: {str(e)}")
            logger.error(f"Mod display error: {str(e)}")

    def create_mod_card(self, mod):
        widget = QWidget()
        widget.setProperty("mod_id", mod['project_id'])
        widget.setFixedHeight(100)
        
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)
        
        icon_label = QLabel()
        icon_label.setFixedSize(80, 80)
        icon_label.setStyleSheet("background-color: #353535;")
        layout.addWidget(icon_label)
        
        text_layout = QVBoxLayout()
        title = QLabel(f"<b>{mod['title']}</b>")
        title.setStyleSheet("color: white; font-size: 14px;")
        desc = QLabel(mod.get('description', 'No description'))
        desc.setStyleSheet("color: #AAAAAA;")
        desc.setWordWrap(True)
        text_layout.addWidget(title)
        text_layout.addWidget(desc)
        
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
        
        if mod.get('icon_url'):
            self.load_mod_icon(mod['project_id'], mod['icon_url'])
        else:
            self.set_fallback_icon(mod['project_id'])

    def load_mod_icon(self, mod_id, url):
        loader = ImageLoaderThread(url, mod_id)
        loader.loaded.connect(self.update_mod_icon)
        self.current_image_loaders.append(loader)
        loader.start()

    def update_mod_icon(self, mod_id, pixmap):
        for i in range(self.mod_list_layout.count()):
            widget = self.mod_list_layout.itemAt(i).widget()
            if widget and widget.property("mod_id") == mod_id:
                icon_label = widget.layout().itemAt(0).widget()
                icon_label.setPixmap(pixmap.scaled(
                    80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation
                ))
                break

    def set_fallback_icon(self, mod_id):
        pixmap = QPixmap(80, 80)
        pixmap.fill(QColor(53, 53, 53))
        painter = QPainter(pixmap)
        painter.setPen(Qt.white)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "No Image")
        painter.end()
        self.update_mod_icon(mod_id, pixmap)

    def install_mod(self, mod):
        if not self.current_server:
            QMessageBox.warning(self, "Error", "Select a server first!")
            return
            
        try:
            server = self.servers[self.current_server]
            mc_version = server.get('mc_version', 'unknown')
            
            versions = requests.get(
                f"https://api.modrinth.com/v2/project/{mod['project_id']}/version",
                headers={'Authorization': self.settings.get('modrinth_key', '')},
                timeout=10
            ).json()
            
            compatible_versions = [v for v in versions if mc_version in v.get('game_versions', [])]
            if not compatible_versions:
                QMessageBox.warning(self, "Error", 
                    f"No versions compatible with {mc_version} found!")
                return
                
            version = compatible_versions[0]
            file = version['files'][0]
            
            server_path = server['path']
            mods_dir = os.path.join(server_path, "mods")
            os.makedirs(mods_dir, exist_ok=True)
            
            self.download_file(file['url'], os.path.join(mods_dir, file['filename']))
            QMessageBox.information(self, "Success", f"Installed {mod['title']}!")
            logger.info(f"Installed mod: {mod['title']}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Install failed: {str(e)}")
            logger.error(f"Mod installation error: {str(e)}")

    def show_mod_error(self, error):
        self.progress.hide()
        QMessageBox.critical(self, "Search Error", f"Mod search failed: {error}")
        logger.error(f"Mod search error: {error}")

    def safe_plugin_search(self):
        query = self.plugin_search.text()
        if not query or not self.settings.get('curseforge_key'):
            QMessageBox.warning(self, "Error", "CurseForge API key required!")
            return
            
        try:
            headers = {'x-api-key': self.settings['curseforge_key']}
            response = requests.post(
                'https://api.curseforge.com/v1/mods/search',
                headers=headers,
                json={
                    'gameId': 432,
                    'searchFilter': query,
                    'classId': 5
                },
                timeout=10
            )
            response.raise_for_status()
            self.show_plugins(response.json()['data'])
            logger.info(f"Plugin search completed: {query}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Plugin search failed: {str(e)}")
            logger.error(f"Plugin search error: {str(e)}")

    def show_plugins(self, plugins):
        while self.plugin_list_layout.count():
            item = self.plugin_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        try:
            for plugin in plugins:
                self.create_plugin_card(plugin)
            self.plugin_list_layout.addStretch()
            logger.info(f"Displayed {len(plugins)} plugins")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to display plugins: {str(e)}")
            logger.error(f"Plugin display error: {str(e)}")

    def create_plugin_card(self, plugin):
        widget = QWidget()
        widget.setFixedHeight(100)
        
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)
        
        text_layout = QVBoxLayout()
        title = QLabel(f"<b>{plugin['name']}</b>")
        title.setStyleSheet("color: white; font-size: 14px;")
        desc = QLabel(plugin.get('summary', 'No description'))
        desc.setStyleSheet("color: #AAAAAA;")
        desc.setWordWrap(True)
        text_layout.addWidget(title)
        text_layout.addWidget(desc)
        
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

    def install_plugin(self, plugin):
        if not self.current_server:
            QMessageBox.warning(self, "Error", "Select a server first!")
            return
            
        try:
            server_path = self.servers[self.current_server]['path']
            plugins_dir = os.path.join(server_path, "plugins")
            os.makedirs(plugins_dir, exist_ok=True)
            
            file = plugin['latestFiles'][0]
            download_url = file['downloadUrl']
            
            if not download_url:
                QMessageBox.warning(self, "Error", "No download available!")
                return
                
            self.download_file(download_url, os.path.join(plugins_dir, file['fileName']))
            QMessageBox.information(self, "Success", f"Installed {plugin['name']}!")
            logger.info(f"Installed plugin: {plugin['name']}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Install failed: {str(e)}")
            logger.error(f"Plugin installation error: {str(e)}")

    def load_settings(self):
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    self.settings = json.load(f)
                    logger.info("Loaded settings")
        except Exception as e:
            logger.error(f"Settings load error: {str(e)}")

    def save_settings(self):
        try:
            self.settings['curseforge_key'] = self.curseforge_key.text()
            self.settings['modrinth_key'] = self.modrinth_key.text()
            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, f)
            logger.info("Saved settings")
            QMessageBox.information(self, "Success", "Settings saved!")
        except Exception as e:
            logger.error(f"Settings save error: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to save settings: {str(e)}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    qdarktheme.setup_theme()
    window = ServerManager()
    window.show()
    sys.exit(app.exec())