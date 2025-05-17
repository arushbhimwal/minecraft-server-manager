#!/usr/bin/env python3
import sys
import os
import platform
import json
import subprocess
import requests
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QComboBox, QPushButton, QLabel, QTabWidget, QTextEdit,
                               QLineEdit, QFileDialog, QMessageBox, QTreeView, QFileSystemModel,
                               QInputDialog)  # Added missing import
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import Qt, QThread, Signal, QDir
import qdarktheme
from mcrcon import MCRcon

class ServerManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Minecraft Server Manager")
        self.setGeometry(100, 100, 1200, 800)
        self.setup_ui()
        self.load_profiles()
        self.check_java()
        
        # Initialize components
        self.servers = {}
        self.current_server = None
        self.java_versions = ['8', '11', '17', '21']
        self.loaders = ['Vanilla', 'Paper', 'Spigot', 'Forge', 'Fabric', 'Quilt', 'Mohist']

    def setup_ui(self):
        # Main layout
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        # Menu Bar
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        new_action = QAction("New Server", self)
        new_action.triggered.connect(self.create_new_server)
        file_menu.addAction(new_action)

        # Top controls
        top_layout = QHBoxLayout()
        self.java_combo = QComboBox()
        self.java_combo.addItems(self.java_versions)
        top_layout.addWidget(QLabel("Java Version:"))
        top_layout.addWidget(self.java_combo)
        
        self.loader_combo = QComboBox()
        self.loader_combo.addItems(self.loaders)
        top_layout.addWidget(QLabel("Server Loader:"))
        top_layout.addWidget(self.loader_combo)
        
        self.version_combo = QComboBox()
        top_layout.addWidget(QLabel("Minecraft Version:"))
        top_layout.addWidget(self.version_combo)
        
        layout.addLayout(top_layout)

        # Tab widget
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Server Console Tab
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
        file_layout.addWidget(self.file_view)
        self.tabs.addTab(self.file_tab, "Files")

        # Mods Tab
        self.mods_tab = QWidget()
        mods_layout = QVBoxLayout(self.mods_tab)
        self.mod_search = QLineEdit()
        self.mod_list = QTextEdit()
        mods_layout.addWidget(QLabel("Search Mods:"))
        mods_layout.addWidget(self.mod_search)
        mods_layout.addWidget(self.mod_list)
        self.tabs.addTab(self.mods_tab, "Mods")

    def create_new_server(self):
        # Server creation dialog
        server_name, ok = QInputDialog.getText(self, "New Server", "Server name:")
        if ok and server_name:
            self.setup_server(server_name)

    def setup_server(self, name):
        # Create server directory
        server_dir = os.path.join(os.getcwd(), "servers", name)
        os.makedirs(server_dir, exist_ok=True)
        
        # Download server JAR based on loader
        loader = self.loader_combo.currentText()
        version = self.version_combo.currentText()
        
        if loader == "Vanilla":
            jar_url = self.get_vanilla_url(version)
        elif loader == "Paper":
            jar_url = self.get_paper_url(version)
        
        # Download JAR file
        self.download_file(jar_url, os.path.join(server_dir, "server.jar"))
        
        # Write eula.txt
        with open(os.path.join(server_dir, "eula.txt"), 'w') as f:
            f.write("eula=true\n")
            
        # Add to server list
        self.servers[name] = {
            "path": server_dir,
            "status": "stopped",
            "process": None
        }
        self.update_server_list()

    def get_vanilla_url(self, version):
        manifest = requests.get("https://piston-meta.mojang.com/mc/game/version_manifest.json").json()
        for v in manifest['versions']:
            if v['id'] == version:
                version_data = requests.get(v['url']).json()
                return version_data['downloads']['server']['url']
        return None

    def get_paper_url(self, version):
        builds = requests.get(f"https://api.papermc.io/v2/projects/paper/versions/{version}").json()
        latest_build = builds['builds'][-1]
        return f"https://api.papermc.io/v2/projects/paper/versions/{version}/builds/{latest_build}/downloads/paper-{version}-{latest_build}.jar"

    def check_java(self):
        try:
            result = subprocess.run(['java', '-version'], capture_output=True, text=True)
            if result.returncode == 0:
                self.java_combo.setCurrentText(result.stderr.split()[2].strip('"'))
        except FileNotFoundError:
            QMessageBox.warning(self, "Java Not Found", "Java not detected! Please install Java SE.")

    def send_command(self):
        cmd = self.command_input.text()
        self.command_input.clear()
        try:
            with MCRcon("localhost", "password", 25575) as mcr:
                response = mcr.command(cmd)
                self.console_output.append(f"> {cmd}\n{response}")
        except Exception as e:
            self.console_output.append(f"Error: {str(e)}")

    def download_file(self, url, path):
        # Implement async download with progress
        response = requests.get(url)
        with open(path, 'wb') as f:
            f.write(response.content)

    def update_server_list(self):
        # Update UI with current servers
        pass

if __name__ == "__main__":
    app = QApplication(sys.argv)
    qdarktheme.setup_theme()
    window = ServerManager()
    window.show()
    sys.exit(app.exec())