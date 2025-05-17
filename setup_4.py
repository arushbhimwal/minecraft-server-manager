import sys
import os
import platform
import subprocess
import webbrowser
import json
from pathlib import Path

import requests
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QLabel, QPushButton, QTabWidget, QTextEdit, QLineEdit,
    QFileDialog, QMessageBox, QCheckBox
)
from PySide6.QtGui import QIcon
import qdarktheme

# Supported loaders and their forks
LOADERS = {
    "Vanilla": None,
    "PaperMC": "https://api.papermc.io/v2/projects/paper",
    "Spigot": None,
    "Purpur": "https://api.papermc.io/v2/projects/purpur",
    "Forge": None,
    "Fabric": None,
    "Quilt": None,
    "Mohist": None,
    # Add more forks/endpoints as needed
}

JAVA_VERSIONS = ["17", "18", "19", "20"]  # JDK versions

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Minecraft Server Manager")
        self.setWindowIcon(QIcon.fromTheme("game-server"))
        self.resize(1000, 700)

        # Main tabs
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.setup_tab = SetupTab()
        self.mod_tab = ModPluginTab()
        self.console_tab = ConsoleTab()
        self.settings_tab = SettingsTab()

        self.tabs.addTab(self.setup_tab, "Server Setup")
        self.tabs.addTab(self.mod_tab, "Mods & Plugins")
        self.tabs.addTab(self.console_tab, "Console")
        self.tabs.addTab(self.settings_tab, "Settings")

        # Status bar
        self.status = self.statusBar()

class SetupTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()

        # OS and Architecture
        os_label = QLabel(f"OS: {platform.system()} {platform.machine()}")
        layout.addWidget(os_label)

        # Java version dropdown
        layout.addWidget(QLabel("Select JDK version to install:"))
        self.java_combo = QComboBox()
        self.java_combo.addItems(JAVA_VERSIONS)
        layout.addWidget(self.java_combo)

        # Loader dropdown
        layout.addWidget(QLabel("Select Server Loader:"))
        self.loader_combo = QComboBox()
        self.loader_combo.addItems(list(LOADERS.keys()))
        self.loader_combo.currentTextChanged.connect(self.on_loader_change)
        layout.addWidget(self.loader_combo)

        # Version dropdown
        layout.addWidget(QLabel("Select Minecraft Version:"))
        self.version_combo = QComboBox()
        layout.addWidget(self.version_combo)

        # Buttons
        btn_layout = QHBoxLayout()
        self.create_btn = QPushButton("Create Server")
        self.create_btn.clicked.connect(self.create_server)
        btn_layout.addWidget(self.create_btn)

        self.edit_btn = QPushButton("Edit Files")
        self.edit_btn.clicked.connect(self.open_folder)
        btn_layout.addWidget(self.edit_btn)

        layout.addLayout(btn_layout)
        self.setLayout(layout)
        self.on_loader_change(self.loader_combo.currentText())

    def on_loader_change(self, loader):
        self.version_combo.clear()
        endpoint = LOADERS.get(loader)
        if endpoint:
            try:
                data = requests.get(endpoint).json()
                versions = data.get("versions", []) or data.get("builds", [])
            except Exception:
                versions = []
        else:
            # Mojang manifest for Vanilla and others
            try:
                manifest = requests.get(
                    "https://piston-meta.mojang.com/mc/game/version_manifest.json"
                ).json()
                versions = [v["id"] for v in manifest.get("versions", [])]
            except Exception:
                versions = []
        self.version_combo.addItems(versions)

    def create_server(self):
        loader = self.loader_combo.currentText()
        version = self.version_combo.currentText()
        jdk = self.java_combo.currentText()
        folder = QFileDialog.getExistingDirectory(self, "Select Server Directory")
        if not folder:
            return
        os.makedirs(folder, exist_ok=True)
        # Check Java
        if not self.is_java_installed():
            if QMessageBox.question(
                self, "Install Java?", "Java not found. Download JDK?"
            ) == QMessageBox.Yes:
                webbrowser.open(f"https://adoptium.net/?version={jdk}")
                return
        # Download server jar
        # TODO: implement download logic per loader
        # Write eula
        with open(os.path.join(folder, "eula.txt"), 'w') as f:
            f.write("eula=true\n")
        QMessageBox.information(self, "Success", f"Server {loader} {version} initialized.")

    def is_java_installed(self):
        try:
            res = subprocess.run(["java", "-version"], capture_output=True)
            return res.returncode == 0
        except FileNotFoundError:
            return False

    def open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Server Directory")
        if folder:
            if platform.system() == "Windows":
                os.startfile(folder)
            elif platform.system() == "Darwin":
                subprocess.run(["open", folder])
            else:
                subprocess.run(["xdg-open", folder])

class ModPluginTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search Modrinth or CurseForge...")
        layout.addWidget(self.search)
        btn_layout = QHBoxLayout()
        self.modrinth_btn = QPushButton("Search Modrinth")
        self.modrinth_btn.clicked.connect(self.search_modrinth)
        btn_layout.addWidget(self.modrinth_btn)
        self.curseforge_btn = QPushButton("Search CurseForge")
        self.curseforge_btn.clicked.connect(self.search_curseforge)
        btn_layout.addWidget(self.curseforge_btn)
        layout.addLayout(btn_layout)
        self.results = QTextEdit()
        self.results.setReadOnly(True)
        layout.addWidget(self.results)
        self.setLayout(layout)

    def search_modrinth(self):
        query = self.search.text().strip()
        if not query:
            return
        url = f"https://api.modrinth.com/v2/search?query={query}"
        data = requests.get(url).json()
        hits = data.get("hits", [])
        self.results.clear()
        for h in hits[:10]:
            self.results.append(f"{h.get('title')} - {h.get('project_id')}")

    def search_curseforge(self):
        # Requires API key configuration in Settings
        QMessageBox.information(self, "Info", "CurseForge search not implemented.")

class ConsoleTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)
        cmd_layout = QHBoxLayout()
        self.cmd = QLineEdit()
        cmd_layout.addWidget(self.cmd)
        send = QPushButton("Send")
        send.clicked.connect(self.send_cmd)
        cmd_layout.addWidget(send)
        layout.addLayout(cmd_layout)
        self.setLayout(layout)

    def send_cmd(self):
        cmd = self.cmd.text().strip()
        if cmd:
            # TODO: implement RCON or subprocess interaction
            self.log.append(f"> {cmd}\n(Command execution not yet implemented)")

class SettingsTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        layout.addWidget(QLabel("CurseForge API Key:"))
        self.api_key = QLineEdit()
        layout.addWidget(self.api_key)
        save = QPushButton("Save Settings")
        save.clicked.connect(self.save)
        layout.addWidget(save)
        self.setLayout(layout)

    def save(self):
        cfg = {"curseforge_key": self.api_key.text().strip()}
        with open(Path.home() / ".mc_manager_cfg.json", 'w') as f:
            json.dump(cfg, f, indent=2)
        QMessageBox.information(self, "Saved", "Settings saved.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    qdarktheme.setup_theme()
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
