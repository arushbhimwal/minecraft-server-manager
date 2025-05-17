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
    QInputDialog, QListWidget, QListWidgetItem, QProgressBar
)
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import Qt, QThread, Signal, QDir, QTimer
import qdarktheme
from mcrcon import MCRcon

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

class ServerManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.java_versions = ['8', '11', '17', '21']
        self.loaders = ['Vanilla', 'Paper', 'Spigot', 'Forge', 'Fabric', 'Quilt', 'Mohist']
        self.servers = {}
        self.current_server = None
        self.profiles_file = "profiles.json"
        
        self.setWindowTitle("Minecraft Server Manager")
        self.setGeometry(100, 100, 1200, 800)
        self.setup_ui()
        self.load_profiles()
        self.check_java()

    def setup_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # Server List Panel
        self.server_list_panel = QWidget()
        server_list_layout = QVBoxLayout(self.server_list_panel)
        
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
        main_layout.addWidget(self.server_list_panel, stretch=1)

        # Main Content Panel
        content_panel = QWidget()
        content_layout = QVBoxLayout(content_panel)
        
        # Top Control Bar
        top_layout = QHBoxLayout()
        self.java_combo = QComboBox()
        self.java_combo.addItems(self.java_versions)
        top_layout.addWidget(QLabel("Java Version:"))
        top_layout.addWidget(self.java_combo)
        
        self.loader_combo = QComboBox()
        self.loader_combo.addItems(self.loaders)
        self.loader_combo.currentTextChanged.connect(self.update_versions)
        top_layout.addWidget(QLabel("Server Loader:"))
        top_layout.addWidget(self.loader_combo)
        
        self.version_combo = QComboBox()
        top_layout.addWidget(QLabel("Minecraft Version:"))
        top_layout.addWidget(self.version_combo)
        
        content_layout.addLayout(top_layout)

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
        self.mod_search = QLineEdit()
        self.mod_search.setPlaceholderText("Search Modrinth...")
        self.mod_list = QListWidget()
        mods_layout.addWidget(QLabel("Search Mods:"))
        mods_layout.addWidget(self.mod_search)
        mods_layout.addWidget(self.mod_list)
        self.tabs.addTab(self.mods_tab, "Mods")

        main_layout.addWidget(content_panel, stretch=3)
        self.update_controls()

    def create_new_server(self):
        server_name, ok = QInputDialog.getText(
            self, 
            "New Server", 
            "Server name:",
            QLineEdit.Normal,
            ""
        )
        if ok and server_name:
            self.setup_server(server_name)

    def setup_server(self, name):
        server_dir = os.path.join(os.getcwd(), "servers", name)
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
                
                # Create essential files
                with open(os.path.join(server_dir, "eula.txt"), 'w') as f:
                    f.write("eula=true\n")
                
                # Initialize server properties
                self.create_server_properties(server_dir)
                
                self.servers[name] = {
                    "path": server_dir,
                    "status": "stopped",
                    "max_ram": "2G"
                }
                self.save_profiles()
                self.update_server_list()
                QMessageBox.information(self, "Success", f"Server '{name}' created successfully!")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create server: {str(e)}")

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
                    self.servers = json.load(f)
                    self.update_server_list()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load profiles: {str(e)}")

    def save_profiles(self):
        try:
            with open(self.profiles_file, 'w') as f:
                json.dump(self.servers, f, indent=2)
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