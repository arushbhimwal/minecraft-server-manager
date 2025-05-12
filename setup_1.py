#!/usr/bin/env python3
import sys, os, json, subprocess, webbrowser, requests
from PySide6.QtWidgets import *
from PySide6.QtGui import QIcon
import qdarktheme

# Import Docker and RCON libraries
try:
    import docker
except ImportError:
    docker = None
try:
    from mcrcon import MCRcon
except ImportError:
    MCRcon = None

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Minecraft Server Manager")
        self.setWindowIcon(QIcon("app_icon.png"))
        self.resize(800, 600)
        # Create menu, toolbar, tabs
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        # Initialize tabs
        self.setup_tab = ServerSetupTab()
        self.mod_tab = ModPluginTab()
        self.console_tab = ConsoleTab()
        self.settings_tab = SettingsTab()
        self.tabs.addTab(self.setup_tab, "Server Setup")
        self.tabs.addTab(self.mod_tab, "Mods/Plugins")
        self.tabs.addTab(self.console_tab, "Console")
        self.tabs.addTab(self.settings_tab, "Settings")
        self.status = QStatusBar()
        self.setStatusBar(self.status)

class ServerSetupTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        # Loader selection
        self.loader_combo = QComboBox()
        self.loader_combo.addItems(["Vanilla", "Paper", "Spigot", "Forge", "Fabric", "Quilt", "Mojang"]) 
        self.loader_combo.currentTextChanged.connect(self.loader_changed)
        layout.addWidget(QLabel("Server Loader:"))
        layout.addWidget(self.loader_combo)
        # Version selection
        self.version_combo = QComboBox()
        layout.addWidget(QLabel("Minecraft Version:"))
        layout.addWidget(self.version_combo)
        # Java path / detection
        java_installed, ver_info = check_java()
        self.java_label = QLabel("Java: " + (ver_info if java_installed else "Not found"))
        layout.addWidget(self.java_label)
        if not java_installed:
            btn = QPushButton("Download Java")
            btn.clicked.connect(self.download_java)
            layout.addWidget(btn)
        # Docker checkbox
        self.docker_checkbox = QCheckBox("Use Docker")
        layout.addWidget(self.docker_checkbox)
        # Setup button
        setup_btn = QPushButton("Create Server")
        setup_btn.clicked.connect(self.create_server)
        layout.addWidget(setup_btn)
        self.setLayout(layout)

    def loader_changed(self, loader_name):
        self.version_combo.clear()
        if loader_name == "Paper":
            versions = fetch_paper_versions()  # Uses PaperMC API:contentReference[oaicite:23]{index=23}
        else:
            versions = fetch_vanilla_versions()  # Uses Mojang manifest:contentReference[oaicite:24]{index=24}
        self.version_combo.addItems(versions)

    def download_java(self):
        webbrowser.open("https://adoptium.net/")  # Open Java download page

    def create_server(self):
        loader = self.loader_combo.currentText().lower()
        version = self.version_combo.currentText()
        use_docker = self.docker_checkbox.isChecked()
        profile_name = f"{loader}_{version}"
        folder = os.path.join(os.getcwd(), profile_name)
        os.makedirs(folder, exist_ok=True)
        # Download appropriate server jar...
        # ...
        # Write eula.txt
        with open(os.path.join(folder, "eula.txt"), 'w') as f:
            f.write("eula=true\n")
        # Write default server.properties
        props = {"motd": "My Minecraft Server", "max-players": "20"}
        with open(os.path.join(folder, "server.properties"), 'w') as f:
            for k, v in props.items():
                f.write(f"{k}={v}\n")
        self.parentWidget().parentWidget().statusBar().showMessage(f"Server '{profile_name}' created", 5000)

class ModPluginTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search mods/plugins...")
        self.search_btn = QPushButton("Search Modrinth")
        self.search_btn.clicked.connect(self.search_mods)
        layout.addWidget(self.search_bar)
        layout.addWidget(self.search_btn)
        self.results_list = QListWidget()
        layout.addWidget(self.results_list)
        install_btn = QPushButton("Install Selected")
        install_btn.clicked.connect(self.install_selected_mod)
        layout.addWidget(install_btn)
        self.setLayout(layout)
        self.found_mods = []

    def search_mods(self):
        query = self.search_bar.text()
        if not query: return
        # Example: use Modrinth API (no auth required)
        resp = requests.get(f"https://api.modrinth.com/v2/search?query={query}")
        data = resp.json()
        self.results_list.clear()
        self.found_mods = []
        for proj in data.get("hits", []):
            name = proj.get("title", "")
            mod_id = proj.get("project_id", "")
            self.found_mods.append((name, mod_id))
            self.results_list.addItem(name)

    def install_selected_mod(self):
        idx = self.results_list.currentRow()
        if idx < 0: return
        name, mod_id = self.found_mods[idx]
        # Fetch project details to get latest file
        resp = requests.get(f"https://api.modrinth.com/v2/project/{mod_id}")
        project = resp.json()
        latest_version = project["versions"][0] if project["versions"] else None
        if latest_version:
            ver_data = requests.get(f"https://api.modrinth.com/v2/version/{latest_version}").json()
            download_url = ver_data["files"][0]["url"]
            # Download .jar into mods/ or plugins/
            mods_folder = os.path.join(os.getcwd(), "mods")
            os.makedirs(mods_folder, exist_ok=True)
            jar_path = os.path.join(mods_folder, f"{name}.jar")
            r = requests.get(download_url)
            with open(jar_path, 'wb') as f:
                f.write(r.content)
            QMessageBox.information(self, "Installed", f"Installed {name}")
        else:
            QMessageBox.warning(self, "Error", "No version found.")

class ConsoleTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        layout.addWidget(self.log_output)
        hbox = QHBoxLayout()
        self.cmd_input = QLineEdit()
        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self.send_command)
        hbox.addWidget(self.cmd_input)
        hbox.addWidget(send_btn)
        layout.addLayout(hbox)
        self.setLayout(layout)
        self.rcon_password = "changeme"  # Should be set from server.properties
        self.rcon_host = "localhost"
        self.rcon_port = 25575

    def send_command(self):
        cmd = self.cmd_input.text().strip()
        if not cmd: return
        if MCRcon:
            try:
                with MCRcon(self.rcon_host, self.rcon_password, port=self.rcon_port) as mcr:
                    resp = mcr.command(cmd)  # Send via RCON:contentReference[oaicite:25]{index=25}
                    self.log_output.append(f"> {cmd}\n{resp}")
            except Exception as e:
                self.log_output.append(f"RCON error: {e}")
        else:
            self.log_output.append("MCRcon library not installed.")

class SettingsTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.profile_list = QListWidget()
        layout.addWidget(QLabel("Saved Profiles:"))
        layout.addWidget(self.profile_list)
        load_btn = QPushButton("Load Profile")
        load_btn.clicked.connect(self.load_profile)
        layout.addWidget(load_btn)
        self.setLayout(layout)
        self.load_profiles()

    def load_profiles(self):
        if os.path.exists("profiles.json"):
            with open("profiles.json", 'r') as f:
                profiles = json.load(f)
            self.profile_list.clear()
            for prof in profiles:
                self.profile_list.addItem(prof.get("name", "Unnamed"))

    def load_profile(self):
        idx = self.profile_list.currentRow()
        if idx < 0: return
        with open("profiles.json", 'r') as f:
            profiles = json.load(f)
        profile = profiles[idx]
        # Populate GUI fields based on profile data...
        QMessageBox.information(self, "Profile Loaded", f"Loaded profile {profile['name']}")

def check_java():
    try:
        result = subprocess.run(["java", "-version"], capture_output=True, text=True)
        if result.returncode == 0:
            ver_line = result.stderr.splitlines()[0]
            return True, ver_line  # Java is installed
    except Exception:
        return False, None
    return False, None

def fetch_paper_versions():
    try:
        data = requests.get("https://api.papermc.io/v2/projects/paper").json()
        return data.get("versions", [])
    except:
        return []

def fetch_vanilla_versions():
    try:
        data = requests.get("https://piston-meta.mojang.com/mc/game/version_manifest.json").json()
        return [v["id"] for v in data.get("versions", [])]
    except:
        return []

if __name__ == "__main__":
    app = QApplication(sys.argv)
    qdarktheme.setup_theme()  # Dark mode:contentReference[oaicite:26]{index=26}
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
