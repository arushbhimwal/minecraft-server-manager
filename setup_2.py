#!/usr/bin/env python3
"""
Minecraft Server Manager Template
A skeleton for a PySide6-based cross-platform GUI application to configure and run Minecraft servers.
Features:
- Loader selection (Vanilla, Paper, Spigot, Forge, Fabric, etc.)
- Version fetching via APIs
- Java detection and prompting
- Optional Docker deployment
- Embedded RCON console
- Mod/Plugin management via Modrinth and CurseForge
- Profile persistence
- MVC-style modular structure
"""
import sys
import os
import json
import subprocess
import webbrowser
from pathlib import Path

# Qt imports
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget,
    QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QCheckBox, QLineEdit, QListWidget,
    QTextEdit, QMessageBox, QStatusBar
)
from PySide6.QtGui import QIcon

# Optional dependencies
try:
    import docker
except ImportError:
    docker = None

try:
    from mcrcon import MCRcon
except ImportError:
    MCRcon = None

# Utilities

def check_java(required_major=17):
    """Return (installed: bool, version_info: str)"""
    try:
        result = subprocess.run(["java", "-version"], capture_output=True, text=True)
        ver_line = result.stderr.splitlines()[0]
        # parse major version
        # TODO: implement regex to extract major
        return True, ver_line
    except Exception:
        return False, None

# API modules stubs
class VersionFetcher:
    @staticmethod
    def fetch_vanilla():
        """Fetch version list from Mojang manifest"""
        # TODO: requests.get + parse
        return []

    @staticmethod
    def fetch_paper():
        """Fetch version list from PaperMC API"""
        return []

# Mod/Plugin manager stub
class ModManager:
    def search_modrinth(self, query: str):
        # TODO: call Modrinth API
        return []

    def search_curseforge(self, query: str):
        # TODO: use curseforge library
        return []

    def install_mod(self, mod_id: str, dest_folder: Path):
        # TODO: download and save
        pass

# Docker manager stub
class DockerManager:
    def __init__(self):
        # TODO: connect to Docker
        pass

    def run_container(self, image: str, volumes: dict):
        # TODO: container run
        pass

# RCON console stub
class ConsoleClient:
    def __init__(self, host: str, port: int, password: str):
        self.host = host
        self.port = port
        self.password = password

    def send(self, command: str) -> str:
        if not MCRcon:
            return "MCRcon library not available"
        with MCRcon(self.host, self.password, port=self.port) as mcr:
            return mcr.command(command)

# Main GUI components
class ServerSetupTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        # Loader combo
        self.loader_combo = QComboBox()
        self.loader_combo.addItems(["Vanilla", "Paper", "Spigot", "Forge", "Fabric"])
        self.loader_combo.currentTextChanged.connect(self.on_loader_change)
        layout.addWidget(QLabel("Server Loader:"))
        layout.addWidget(self.loader_combo)

        # Version combo
        self.version_combo = QComboBox()
        layout.addWidget(QLabel("Minecraft Version:"))
        layout.addWidget(self.version_combo)

        # Java detection
        installed, info = check_java()
        self.java_label = QLabel(f"Java: {info if installed else 'Not found'}")
        layout.addWidget(self.java_label)
        if not installed:
            btn = QPushButton("Download Java")
            btn.clicked.connect(lambda: webbrowser.open("https://adoptium.net/"))
            layout.addWidget(btn)

        # Docker option
        self.docker_checkbox = QCheckBox("Use Docker")
        layout.addWidget(self.docker_checkbox)

        # Create server
        create_btn = QPushButton("Create Server")
        create_btn.clicked.connect(self.create_server)
        layout.addWidget(create_btn)

        self.setLayout(layout)

    def on_loader_change(self, loader_name: str):
        self.version_combo.clear()
        if loader_name.lower() == "paper":
            versions = VersionFetcher.fetch_paper()
        else:
            versions = VersionFetcher.fetch_vanilla()
        self.version_combo.addItems(versions)

    def create_server(self):
        # TODO: implement create logic: download jar, write eula.txt, server.properties
        pass

class ModPluginTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search mods/plugins...")
        layout.addWidget(self.search_bar)

        btn_box = QHBoxLayout()
        self.modrinth_btn = QPushButton("Search Modrinth")
        self.curseforge_btn = QPushButton("Search CurseForge")
        btn_box.addWidget(self.modrinth_btn)
        btn_box.addWidget(self.curseforge_btn)
        layout.addLayout(btn_box)

        self.results_list = QListWidget()
        layout.addWidget(self.results_list)

        self.setLayout(layout)
        # connect signals
        # TODO

class ConsoleTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

        h = QHBoxLayout()
        self.cmd_input = QLineEdit()
        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self.send_cmd)
        h.addWidget(self.cmd_input)
        h.addWidget(send_btn)
        layout.addLayout(h)

        self.setLayout(layout)
        self.console = ConsoleClient('localhost', 25575, 'changeme')

    def send_cmd(self):
        cmd = self.cmd_input.text().strip()
        if not cmd:
            return
        resp = self.console.send(cmd)
        self.log.append(f"> {cmd}\n{resp}")

class SettingsTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Saved Profiles:"))
        self.list = QListWidget()
        layout.addWidget(self.list)
        load_btn = QPushButton("Load Profile")
        layout.addWidget(load_btn)
        self.setLayout(layout)
        # TODO: implement load profiles

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Minecraft Server Manager")
        self.setWindowIcon(QIcon("app_icon.png"))
        self.resize(900, 700)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.tabs.addTab(ServerSetupTab(), "Server Setup")
        self.tabs.addTab(ModPluginTab(), "Mods/Plugins")
        self.tabs.addTab(ConsoleTab(), "Console")
        self.tabs.addTab(SettingsTab(), "Settings")

        self.status = QStatusBar()
        self.setStatusBar(self.status)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    # TODO: apply dark theme if desired
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
