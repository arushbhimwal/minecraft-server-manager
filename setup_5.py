#!/usr/bin/env python3
import sys
import os
import platform
import json
import subprocess
import requests
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QTabWidget, QTextEdit,
    QLineEdit, QFileDialog, QMessageBox, QTreeView, QFileSystemModel,
    QInputDialog, QListWidget, QListWidgetItem, QProgressBar,
    QScrollArea, QSizePolicy
)
from PySide6.QtGui import QIcon, QAction, QPixmap, QImage
from PySide6.QtCore import Qt, QThread, Signal, QDir, QTimer, QSize
import qdarktheme
from mcrcon import MCRcon

# ========================
# THREAD CLASSES
# ========================

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
        
        self.stopped.emit()

    def stop(self):
        self.running = False
        if self.process:
            self.process.terminate()

class ModSearchThread(QThread):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, query):
        super().__init__()
        self.query = query

    def run(self):
        try:
            response = requests.get(
                f"https://api.modrinth.com/v2/search?query={self.query}&facets=[[\"categories:forge\"]]"
            )
            response.raise_for_status()
            data = response.json()
            self.finished.emit(data['hits'])
        except Exception as e:
            self.error.emit(str(e))

class ImageLoaderThread(QThread):
    loaded = Signal(str, QPixmap)

    def __init__(self, url, mod_id):
        super().__init__()
        self.url = url
        self.mod_id = mod_id

    def run(self):
        try:
            response = requests.get(self.url)
            image = QImage()
            image.loadFromData(response.content)
            pixmap = QPixmap.fromImage(image)
            self.loaded.emit(self.mod_id, pixmap)
        except:
            self.loaded.emit(self.mod_id, QPixmap())

# ========================
# MAIN APPLICATION
# ========================

class ServerManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.java_versions = ['8', '11', '17', '21']
        self.loaders = ['Vanilla', 'Paper', 'Spigot', 'Forge', 'Fabric', 'Quilt', 'Mohist']
        self.servers = {}
        self.current_server = None
        self.profiles_file = "profiles.json"
        self.mod_icons = {}
        self.current_mods = []
        
        self.setWindowTitle("Minecraft Server Manager")
        self.setGeometry(100, 100, 1200, 800)
        self.setup_ui()
        self.load_profiles()
        self.check_java()

    def setup_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # Left Panel - Server List
        self.server_list_panel = QWidget()
        server_list_layout = QVBoxLayout(self.server_list_panel)
        
        self.server_list = QListWidget()
        self.server_list.itemClicked.connect(self.select_server)
        server_list_layout.addWidget(QLabel("Servers:"))
        server_list_layout.addWidget(self.server_list)
        
        # Server Control Buttons
        self.btn_start = QPushButton("Start Server")
        self.btn_start.clicked.connect(self.start_server)
        self.btn_stop = QPushButton("Stop Server")
        self.btn_stop.clicked.connect(self.stop_server)
        self.btn_delete = QPushButton("Delete Server")
        self.btn_delete.clicked.connect(self.delete_server)
        
        server_list_layout.addWidget(self.btn_start)
        server_list_layout.addWidget(self.btn_stop)
        server_list_layout.addWidget(self.btn_delete)
        main_layout.addWidget(self.server_list_panel, stretch=1)

        # Right Panel - Main Content
        content_panel = QWidget()
        content_layout = QVBoxLayout(content_panel)
        
        # Server Creation Controls
        creation_layout = QHBoxLayout()
        self.server_path = QLineEdit()
        self.server_path.setPlaceholderText("Select server directory...")
        btn_browse = QPushButton("Browse")
        btn_browse.clicked.connect(self.select_server_directory)
        btn_create = QPushButton("Create New Server")
        btn_create.clicked.connect(self.create_new_server)
        
        creation_layout.addWidget(QLabel("Server Location:"))
        creation_layout.addWidget(self.server_path)
        creation_layout.addWidget(btn_browse)
        creation_layout.addWidget(btn_create)
        content_layout.addLayout(creation_layout)

        # Server Configuration
        config_layout = QHBoxLayout()
        self.java_combo = QComboBox()
        self.java_combo.addItems(self.java_versions)
        config_layout.addWidget(QLabel("Java Version:"))
        config_layout.addWidget(self.java_combo)
        
        self.loader_combo = QComboBox()
        self.loader_combo.addItems(self.loaders)
        self.loader_combo.currentTextChanged.connect(self.update_versions)
        config_layout.addWidget(QLabel("Server Loader:"))
        config_layout.addWidget(self.loader_combo)
        
        self.version_combo = QComboBox()
        config_layout.addWidget(QLabel("Minecraft Version:"))
        config_layout.addWidget(self.version_combo)
        content_layout.addLayout(config_layout)

        # Progress Bar
        self.progress = QProgressBar()
        self.progress.hide()
        content_layout.addWidget(self.progress)

        # Tab Widget
        self.tabs = QTabWidget()
        content_layout.addWidget(self.tabs)

        # Console Tab
        self.console_tab = QWidget()
        console_layout = QVBoxLayout(self.console_tab)
        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        console_layout.addWidget(self.console_output)
        self.command_input = QLineEdit()
        self.command_input.returnPressed.connect(self.send_command)
        console_layout.addWidget(self.command_input)
        self.tabs.addTab(self.console_tab, "Console")

        # File Explorer Tab
        self.file_tab = QWidget()
        file_layout = QVBoxLayout(self.file_tab)
        self.file_model = QFileSystemModel()
        self.file_model.setRootPath(QDir.currentPath())
        self.file_view = QTreeView()
        self.file_view.setModel(self.file_model)
        self.file_view.doubleClicked.connect(self.open_file)
        file_layout.addWidget(self.file_view)
        self.tabs.addTab(self.file_tab, "Files")

        # Mods Tab
        self.mods_tab = QWidget()
        mods_layout = QVBoxLayout(self.mods_tab)
        search_layout = QHBoxLayout()
        self.mod_search = QLineEdit()
        self.mod_search.setPlaceholderText("Search Modrinth...")
        btn_search = QPushButton("Search")
        btn_search.clicked.connect(self.start_mod_search)
        search_layout.addWidget(self.mod_search)
        search_layout.addWidget(btn_search)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.mod_list = QWidget()
        self.mod_list_layout = QVBoxLayout(self.mod_list)
        scroll.setWidget(self.mod_list)
        
        mods_layout.addLayout(search_layout)
        mods_layout.addWidget(scroll)
        self.tabs.addTab(self.mods_tab, "Mods")

        main_layout.addWidget(content_panel, stretch=3)
        self.update_controls()

    # ========================
    # CORE FUNCTIONALITY
    # ========================

    def select_server_directory(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select Server Directory", QDir.homePath(), QFileDialog.ShowDirsOnly
        )
        if path:
            self.server_path.setText(path)

    def create_new_server(self):
        if not self.server_path.text():
            QMessageBox.warning(self, "Error", "Please select a server directory first!")
            return

        server_name, ok = QInputDialog.getText(
            self, "New Server", "Server name:", QLineEdit.Normal, ""
        )
        if ok and server_name:
            self.setup_server(server_name)

    def setup_server(self, name):
        server_dir = os.path.join(self.server_path.text(), name)
        os.makedirs(server_dir, exist_ok=True)

        loader = self.loader_combo.currentText()
        version = self.version_combo.currentText()
        
        try:
            if loader == "Vanilla":
                jar_url = self.get_vanilla_url(version)
            elif loader == "Paper":
                jar_url = self.get_paper_url(version)
            
            if jar_url:
                self.download_file(jar_url, os.path.join(server_dir, "server.jar"))
                
                with open(os.path.join(server_dir, "eula.txt"), 'w') as f:
                    f.write("eula=true\n")
                
                self.create_server_properties(server_dir)
                
                self.servers[name] = {
                    "path": server_dir,
                    "status": "stopped",
                    "max_ram": "2G",
                    "thread": None
                }
                self.save_profiles()
                self.update_server_list()
                QMessageBox.information(self, "Success", f"Server '{name}' created successfully!")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create server: {str(e)}")

    # ========================
    # MOD MANAGEMENT
    # ========================

    def start_mod_search(self):
        query = self.mod_search.text()
        if not query:
            return
        
        self.progress.show()
        self.mod_list_layout.setParent(None)
        self.mod_list_layout = QVBoxLayout(self.mod_list)
        self.mod_list.setLayout(self.mod_list_layout)
        
        self.search_thread = ModSearchThread(query)
        self.search_thread.finished.connect(self.show_mod_results)
        self.search_thread.error.connect(self.show_mod_error)
        self.search_thread.start()

    def show_mod_results(self, mods):
        self.progress.hide()
        self.current_mods = mods
        
        for mod in mods:
            mod_widget = QWidget()
            mod_widget.setFixedHeight(100)
            layout = QHBoxLayout(mod_widget)
            
            icon_label = QLabel()
            icon_label.setFixedSize(80, 80)
            layout.addWidget(icon_label)
            
            text_layout = QVBoxLayout()
            title = QLabel(f"<b>{mod['title']}</b>")
            title.setStyleSheet("font-size: 14px;")
            description = QLabel(mod['description'])
            description.setWordWrap(True)
            text_layout.addWidget(title)
            text_layout.addWidget(description)
            
            install_btn = QPushButton("Install")
            install_btn.clicked.connect(lambda _, m=mod: self.install_mod(m))
            
            layout.addLayout(text_layout, 1)
            layout.addWidget(install_btn)
            self.mod_list_layout.addWidget(mod_widget)
            
            self.load_mod_icon(mod['project_id'], mod['icon_url'])
            
        self.mod_list_layout.addStretch()

    def load_mod_icon(self, mod_id, icon_url):
        if mod_id in self.mod_icons:
            return
        
        thread = ImageLoaderThread(icon_url, mod_id)
        thread.loaded.connect(self.set_mod_icon)
        thread.start()

    def set_mod_icon(self, mod_id, pixmap):
        self.mod_icons[mod_id] = pixmap
        for i in range(self.mod_list_layout.count()):
            widget = self.mod_list_layout.itemAt(i).widget()
            if widget and widget.property('mod_id') == mod_id:
                icon_label = widget.layout().itemAt(0).widget()
                icon_label.setPixmap(pixmap.scaled(80, 80, Qt.KeepAspectRatio))

    def show_mod_error(self, error):
        self.progress.hide()
        QMessageBox.critical(self, "Search Error", f"Failed to search mods: {error}")

    def install_mod(self, mod):
        if not self.current_server:
            QMessageBox.warning(self, "Error", "Please select a server first!")
            return
            
        server_path = self.servers[self.current_server]['path']
        mods_dir = os.path.join(server_path, "mods")
        os.makedirs(mods_dir, exist_ok=True)
        
        try:
            versions = requests.get(
                f"https://api.modrinth.com/v2/project/{mod['project_id']}/version"
            ).json()
            
            if not versions:
                raise Exception("No versions available")
                
            version = next((v for v in versions if v['game_versions']), versions[0])
            file = version['files'][0]
            
            mod_path = os.path.join(mods_dir, file['filename'])
            self.download_file(file['url'], mod_path)
            
            QMessageBox.information(self, "Success", 
                f"Installed {mod['title']} successfully!\nRestart server to apply changes.")
                
        except Exception as e:
            QMessageBox.critical(self, "Install Error", 
                f"Failed to install mod: {str(e)}")

    # ========================
    # UTILITY METHODS
    # ========================

    def get_vanilla_url(self, version):
        manifest = requests.get("https://piston-meta.mojang.com/mc/game/version_manifest.json").json()
        for v in manifest['versions']:
            if v['id'] == version and v['type'] == "release":
                version_data = requests.get(v['url']).json()
                return version_data['downloads']['server']['url']
        raise ValueError(f"Version {version} not found")

    def get_paper_url(self, version):
        builds = requests.get(f"https://api.papermc.io/v2/projects/paper/versions/{version}").json()
        if not builds['builds']:
            raise ValueError(f"No builds found for PaperMC {version}")
        latest_build = builds['builds'][-1]
        return f"https://api.papermc.io/v2/projects/paper/versions/{version}/builds/{latest_build}/downloads/paper-{version}-{latest_build}.jar"

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
            QMessageBox.warning(
                self,
                "Java Not Found",
                "Java runtime not detected! Please install Java SE."
            )

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
        except Exception as e:
            self.console_output.append(f"Error: {str(e)}")

    def update_versions(self, loader):
        self.version_combo.clear()
        self.progress.show()
        
        try:
            if loader == "Vanilla":
                response = requests.get("https://piston-meta.mojang.com/mc/game/version_manifest.json")
                data = response.json()
                versions = [v['id'] for v in data['versions'] if v['type'] == 'release']
            elif loader == "Paper":
                response = requests.get("https://api.papermc.io/v2/projects/paper")
                data = response.json()
                versions = data['versions'][::-1]
            elif loader == "Fabric":
                response = requests.get("https://meta.fabricmc.net/v2/versions/game")
                versions = [v['version'] for v in response.json()]
            else:
                versions = ["Version selection not implemented"]
            
            self.version_combo.addItems(versions[:20])
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to fetch versions: {str(e)}")
        finally:
            self.progress.hide()

    def download_file(self, url, path):
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()
            
            with open(path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
        except Exception as e:
            raise Exception(f"Download failed: {str(e)}")

    def create_server_properties(self, server_dir):
        default_props = {
            "server-port": "25565",
            "max-players": "20",
            "online-mode": "true",
            "enable-rcon": "true",
            "rcon.password": "password",
            "rcon.port": "25575"
        }
        
        with open(os.path.join(server_dir, "server.properties"), 'w') as f:
            for key, value in default_props.items():
                f.write(f"{key}={value}\n")

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

    def load_profiles(self):
        try:
            if os.path.exists(self.profiles_file):
                with open(self.profiles_file, 'r') as f:
                    loaded_servers = json.load(f)
                    # Add thread placeholder to loaded servers
                    self.servers = {name: {**data, 'thread': None} 
                                   for name, data in loaded_servers.items()}
                    self.update_server_list()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load profiles: {str(e)}")

    def save_profiles(self):
        try:
            # Create a serializable copy without the thread object
            save_data = {
                name: {k: v for k, v in data.items() if k != 'thread'} 
                for name, data in self.servers.items()
            }
            
            with open(self.profiles_file, 'w') as f:
                json.dump(save_data, f, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save profiles: {str(e)}")

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

    def start_server(self):
        if self.current_server:
            server = self.servers[self.current_server]
            if server['status'] == 'stopped':
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

    def stop_server(self):
        if self.current_server and self.servers[self.current_server]['status'] == 'running':
            self.send_command("stop")
            self.servers[self.current_server]['thread'].stop()
            self.servers[self.current_server]['status'] = 'stopping'
            self.save_profiles()
            self.update_server_list()
            self.update_controls()

    def handle_server_output(self, message):
        self.console_output.append(message)

    def handle_server_stop(self):
        if self.current_server:
            self.servers[self.current_server]['status'] = 'stopped'
            self.servers[self.current_server]['thread'] = None
            self.save_profiles()
            self.update_server_list()
            self.update_controls()

    def delete_server(self):
        if self.current_server:
            reply = QMessageBox.question(
                self,
                "Delete Server",
                f"Are you sure you want to delete '{self.current_server}'?",
                QMessageBox.Yes | QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                server_path = self.servers[self.current_server]['path']
                try:
                    if os.path.exists(server_path):
                        if platform.system() == "Windows":
                            subprocess.run(f"rmdir /s /q {server_path}", shell=True)
                        else:
                            subprocess.run(["rm", "-rf", server_path])
                    del self.servers[self.current_server]
                    self.current_server = None
                    self.save_profiles()
                    self.update_server_list()
                    self.update_controls()
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to delete server: {str(e)}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    qdarktheme.setup_theme()
    window = ServerManager()
    window.show()
    sys.exit(app.exec())