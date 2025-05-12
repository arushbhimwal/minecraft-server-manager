#!/usr/bin/env python3
"""
Minecraft Server Manager
A PySide6-based GUI application to configure and run Minecraft servers.
Features:
- Loader selection (Vanilla, Paper)
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
import shutil
from pathlib import Path

import requests
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget,
    QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QCheckBox, QLineEdit, QListWidget,
    QTextEdit, QMessageBox, QStatusBar, QInputDialog
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

CONFIG_DIR = Path.home() / ".minecraft_server_manager"
CONFIG_DIR.mkdir(exist_ok=True)
PROFILES_FILE = CONFIG_DIR / "profiles.json"

# Utilities

def check_java(required_major=17):
    """Return (installed: bool, version_info: str)"""
    try:
        result = subprocess.run(["java", "-version"], capture_output=True, text=True)
        ver_line = result.stderr.splitlines()[0]
        import re
        match = re.search(r"version \"(\d+)(?:\\.\d+)*\"", ver_line)
        if match and int(match.group(1)) >= required_major:
            return True, ver_line
        return False, ver_line
    except Exception:
        return False, ""

# API modules
class VersionFetcher:
    MOJANG_MANIFEST = "https://launchermeta.mojang.com/mc/game/version_manifest.json"
    PAPER_API = "https://api.papermc.io/v2/projects/paper"

    @staticmethod
    def fetch_vanilla():
        resp = requests.get(VersionFetcher.MOJANG_MANIFEST)
        data = resp.json()
        return [v["id"] for v in data.get("versions", [])]

    @staticmethod
    def fetch_paper():
        resp = requests.get(f"{VersionFetcher.PAPER_API}/versions")
        data = resp.json()
        return data.get("versions", [])

# Mod/Plugin manager
class ModManager:
    MODRINTH_SEARCH = "https://api.modrinth.com/v2/search"
    CURSEFORGE_SEARCH = "https://api.curseforge.com/v1/mods/search"

    def __init__(self):
        self.curseforge_key = os.getenv("CURSEFORGE_API_KEY", "")

    def search_modrinth(self, query: str):
        params = {"query": query, "limit": 20}
        resp = requests.get(self.MODRINTH_SEARCH, params=params)
        return resp.json().get("hits", [])

    def search_curseforge(self, query: str):
        headers = {"x-api-key": self.curseforge_key}
        params = {"search": query, "gameId": 432}
        resp = requests.get(self.CURSEFORGE_SEARCH, headers=headers, params=params)
        return resp.json().get("data", [])

# Docker manager
class DockerManager:
    def __init__(self):
        if docker:
            self.client = docker.from_env()
        else:
            self.client = None

    def run_container(self, image: str, volumes: dict):
        if not self.client:
            raise RuntimeError("Docker SDK not installed")
        return self.client.containers.run(image, detach=True, volumes=volumes)

# RCON console
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

# GUI Tabs
class ServerSetupTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()

        self.loader_combo = QComboBox()
        self.loader_combo.addItems(["Vanilla", "Paper"])
        self.loader_combo.currentTextChanged.connect(self.on_loader_change)
        layout.addWidget(QLabel("Server Loader:"))
        layout.addWidget(self.loader_combo)

        self.version_combo = QComboBox()
        layout.addWidget(QLabel("Minecraft Version:"))
        layout.addWidget(self.version_combo)

        installed, info = check_java()
        self.java_label = QLabel(f"Java: {info if installed else 'Not found/old'}")
        layout.addWidget(self.java_label)
        if not installed:
            btn = QPushButton("Download Java")
            btn.clicked.connect(lambda: webbrowser.open("https://adoptium.net/"))
            layout.addWidget(btn)

        self.docker_checkbox = QCheckBox("Use Docker")
        layout.addWidget(self.docker_checkbox)

        create_btn = QPushButton("Create Server")
        create_btn.clicked.connect(self.create_server)
        layout.addWidget(create_btn)

        self.setLayout(layout)
        self.on_loader_change(self.loader_combo.currentText())

    def on_loader_change(self, loader_name: str):
        self.version_combo.clear()
        try:
            if loader_name.lower() == "paper":
                versions = VersionFetcher.fetch_paper()
            else:
                versions = VersionFetcher.fetch_vanilla()
            self.version_combo.addItems(versions)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to fetch versions: {e}")

    def create_server(self):
        loader = self.loader_combo.currentText().lower()
        version = self.version_combo.currentText()
        target = Path.cwd() / f"minecraft_server_{loader}_{version}"
        target.mkdir(exist_ok=True)

        # Download server jar
        if loader == "paper":
            url = f"https://api.papermc.io/v2/projects/paper/versions/{version}/builds/last/downloads/paper-{version}.jar"
        else:
            manifest = requests.get(VersionFetcher.MOJANG_MANIFEST).json()
            item = next(v for v in manifest["versions"] if v["id"] == version)
            meta = requests.get(item["url"]).json()
            url = meta["downloads"]["server"]["url"]
        jar_path = target / "server.jar"
        with requests.get(url, stream=True) as r:
            with open(jar_path, 'wb') as f:
                shutil.copyfileobj(r.raw, f)

        # Write eula.txt
        eula = target / "eula.txt"
        eula.write_text("eula=true")

        # Default server.properties
        props = target / "server.properties"
        if not props.exists():
            props.write_text("# Generated by Minecraft Server Manager\n")

        QMessageBox.information(self, "Success", f"Server created at {target}")

class ModPluginTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.search_bar = QLineEdit(placeholderText="Search mods/plugins...")
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

        self.manager = ModManager()
        self.modrinth_btn.clicked.connect(self.search_modrinth)
        self.curseforge_btn.clicked.connect(self.search_curseforge)

    def search_modrinth(self):
        query = self.search_bar.text().strip()
        self.results_list.clear()
        for mod in self.manager.search_modrinth(query):
            self.results_list.addItem(f"{mod['title']} (v{mod['latest_version']}) - {mod['project_id']}")

    def search_curseforge(self):
        query = self.search_bar.text().strip()
        self.results_list.clear()
        for mod in self.manager.search_curseforge(query):
            self.results_list.addItem(f"{mod['slug']} (ID {mod['id']})")

class ConsoleTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.log = QTextEdit(readOnly=True)
        layout.addWidget(self.log)

        h = QHBoxLayout()
        self.cmd_input = QLineEdit()
        send_btn = QPushButton("Send")
        h.addWidget(self.cmd_input)
        h.addWidget(send_btn)
        layout.addLayout(h)

        self.setLayout(layout)
        self.console = ConsoleClient('localhost', 25575, 'changeme')
        send_btn.clicked.connect(self.send_cmd)

    def send_cmd(self):
        cmd = self.cmd_input.text().strip()
        if not cmd:
            return
        resp = self.console.send(cmd)
        self.log.append(f"> {cmd}\n{resp}\n")

class SettingsTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Saved Profiles:"))
        self.list = QListWidget()
        layout.addWidget(self.list)
        load_btn = QPushButton("Load Profile")
        save_btn = QPushButton("Save Current Profile")
        layout.addWidget(load_btn)
