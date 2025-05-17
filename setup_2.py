#!/usr/bin/env python3
"""
Minecraft Server Manager
Single-file setup script with a tile-based home page GUI using PySide6.
Features:
 - Server Instances (create with version/download, start, stop, restart)
 - CLI Console for managing plugins/mods
 - File Manager for server files
"""
import sys
import json
import subprocess
import requests
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QStackedWidget, QGridLayout,
    QPushButton, QLabel, QVBoxLayout, QHBoxLayout, QListWidget,
    QListWidgetItem, QInputDialog, QMessageBox, QFileDialog, QComboBox
)
from PySide6.QtGui import QIcon
import qdarktheme

CONFIG_PATH = Path.home() / ".mcs_manager" / "instances.json"

# Utility functions
def load_instances():
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}

def save_instances(instances):
    CONFIG_PATH.write_text(json.dumps(instances, indent=2))

# Instance control
class ServerInstance:
    def __init__(self, name, path, process=None):
        self.name = name
        self.path = path
        self.process = process

    def start(self):
        if self.process and self.process.poll() is None:
            return False
        cmd = ["java", "-jar", "server.jar", "nogui"]
        self.process = subprocess.Popen(cmd, cwd=self.path)
        return True

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            return True
        return False

    def restart(self):
        stopped = self.stop()
        started = self.start()
        return stopped and started

# Main Window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Minecraft Server Manager")
        self.setWindowIcon(QIcon.fromTheme("server"))
        self.resize(800, 600)
        qdarktheme.setup_theme()

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.home_page = HomePage(self)
        self.instances_page = InstancesPage(self)
        self.console_page = ConsolePage(self)
        self.filemgr_page = FileManagerPage(self)

        for page in (self.home_page, self.instances_page, self.console_page, self.filemgr_page):
            self.stack.addWidget(page)

        self.status = self.statusBar()

    def navigate(self, page):
        self.stack.setCurrentWidget(page)

# Home Page
class HomePage(QWidget):
    def __init__(self, parent):
        super().__init__()
        layout = QGridLayout()
        layout.setSpacing(20)
        btn_instances = tile_button("Instances", "overview-server")
        btn_instances.clicked.connect(lambda: parent.navigate(parent.instances_page))
        btn_console = tile_button("CLI Console", "utilities-terminal")
        btn_console.clicked.connect(lambda: parent.navigate(parent.console_page))
        btn_files = tile_button("File Manager", "folder")
        btn_files.clicked.connect(lambda: parent.navigate(parent.filemgr_page))
        placeholder = tile_button("Mods/Plugins", "application-x-executable")
        placeholder.setEnabled(False)
        layout.addWidget(btn_instances, 0, 0)
        layout.addWidget(btn_console, 0, 1)
        layout.addWidget(btn_files, 1, 0)
        layout.addWidget(placeholder, 1, 1)
        self.setLayout(layout)

def tile_button(text, icon_name):
    btn = QPushButton(text)
    btn.setIcon(QIcon.fromTheme(icon_name))
    btn.setMinimumSize(200, 150)
    btn.setStyleSheet(
        "QPushButton { font-size: 18px; border: 2px solid #555; border-radius: 10px; }"
        "QPushButton:hover { background: #444; }"
    )
    return btn

# Instances Page
class InstancesPage(QWidget):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.instances = {}
        self.widgets()
        self.load()

    def widgets(self):
        layout = QVBoxLayout()
        title = QLabel("Server Instances")
        title.setStyleSheet("font-size:24px;font-weight:bold;")
        layout.addWidget(title)
        self.list = QListWidget()
        layout.addWidget(self.list)

        btn_layout = QHBoxLayout()
        for name in ("Create","Start","Stop","Restart","Delete","Back"):
            btn = QPushButton(name)
            btn.clicked.connect(getattr(self, name.lower()))
            btn_layout.addWidget(btn)
        layout.addLayout(btn_layout)
        self.setLayout(layout)

    def load(self):
        data = load_instances()
        self.instances = {n: ServerInstance(n, p) for n, p in data.items()}
        self.refresh_list()

    def refresh_list(self):
        self.list.clear()
        for name, inst in self.instances.items():
            status = "Running" if inst.process and inst.process.poll() is None else "Stopped"
            item = QListWidgetItem(f"{name} — {status}")
            self.list.addItem(item)

    def selected(self):
        item = self.list.currentItem()
        if not item: return None
        name = item.text().split(" — ")[0]
        return self.instances.get(name)

    def create(self):
        # Name & folder
        name, ok = QInputDialog.getText(self, "Create Instance","Enter instance name:")
        if not ok or not name: return
        path = QFileDialog.getExistingDirectory(self, "Select server folder")
        if not path: return
        # Version selection
        manifest = requests.get("https://launchermeta.mojang.com/mc/game/version_manifest.json").json()
        versions = [v["id"] for v in manifest.get("versions",[])]
        version, ok = QInputDialog.getItem(self, "Minecraft Version", "Select version:", versions, editable=False)
        if not ok: return
        # Download server jar
        ver_info = next(v for v in manifest["versions"] if v["id"] == version)
        jar_url = requests.get(ver_info["url"]).json()["downloads"]["server"]["url"]
        r = requests.get(jar_url)
        jar_path = Path(path) / "server.jar"
        jar_path.write_bytes(r.content)
        # Save instance
        self.instances[name] = ServerInstance(name, path)
        save_instances({n: i.path for n, i in self.instances.items()})
        self.refresh_list()

    def start(self): self._action(lambda inst: inst.start(), "start")
    def stop(self): self._action(lambda inst: inst.stop(), "stop")
    def restart(self): self._action(lambda inst: inst.restart(), "restart")
    def delete(self):
        inst = self.selected()
        if not inst: return
        if QMessageBox.question(self, "Delete","Remove instance permanently?", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.instances.pop(inst.name)
        save_instances({n: i.path for n, i in self.instances.items()})
        self.refresh_list()

    def back(self): self.parent.navigate(self.parent.home_page)

    def _action(self, fn, action):
        inst = self.selected()
        if not inst: return
        ok = fn(inst)
        if not ok:
            QMessageBox.warning(self, action.capitalize(), f"Failed to {action} server.")
        self.refresh_list()

# Console Page
class ConsolePage(QWidget):
    def __init__(self, parent):
        super().__init__()
        layout = QVBoxLayout()
        self.log_output = QLabel("[Console placeholder]")
        layout.addWidget(self.log_output)
        back = QPushButton("Back to Home")
        back.clicked.connect(lambda: parent.navigate(parent.home_page))
        layout.addWidget(back)
        self.setLayout(layout)

# File Manager Page
class FileManagerPage(QWidget):
    def __init__(self, parent):
        super().__init__()
        layout = QVBoxLayout()
        self.path_label = QLabel("No server selected")
        layout.addWidget(self.path_label)
        select = QPushButton("Select Server Folder")
        select.clicked.connect(self.select_folder)
        layout.addWidget(select)
        open_btn = QPushButton("Open in File Browser")
        open_btn.clicked.connect(self.open_folder)
        layout.addWidget(open_btn)
        back = QPushButton("Back to Home")
        back.clicked.connect(lambda: parent.navigate(parent.home_page))
        layout.addWidget(back)
        self.setLayout(layout)
        self.server_path = None

    def select_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select Server Directory")
        if path:
            self.server_path = path
            self.path_label.setText(f"Server: {path}")

    def open_folder(self):
        if self.server_path:
            subprocess.Popen(["xdg-open", self.server_path])

# Entry point
if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
