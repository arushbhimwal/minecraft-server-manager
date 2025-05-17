import sys
import os
import requests
import subprocess
from typing import Optional
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel, QComboBox, QLineEdit, QPushButton, QTextEdit,
                               QFileDialog, QMessageBox)
from PySide6.QtCore import QThread, Signal, QObject

class InstallerWorker(QObject):
    progress = Signal(str)
    finished = Signal(bool, str)
    error = Signal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        try:
            self.progress.emit("Starting server installation...")
            installer = ServerInstaller(
                server_type=self.config['type'],
                version=self.config['version'],
                xms=self.config['xms'],
                xmx=self.config['xmx'],
                install_dir=self.config['install_dir']
            )
            installer.install_server()
            self.finished.emit(True, "Installation completed successfully!")
        except Exception as e:
            self.error.emit(f"Error: {str(e)}")
            self.finished.emit(False, str(e))

class ServerInstaller:
    def __init__(self, server_type: str, version: Optional[str] = None,
                 xms: str = "1G", xmx: str = "4G", install_dir: Optional[str] = None):
        self.server_type = server_type.lower()
        self.version = version
        self.xms = xms
        self.xmx = xmx
        self.base_dir = install_dir if install_dir else os.path.join(os.getcwd(), f"{self.server_type}_server")
        self.jar_name = f"{self.server_type}.jar"

    def create_directory(self):
        os.makedirs(self.base_dir, exist_ok=True)

    def write_eula(self):
        eula_path = os.path.join(self.base_dir, "eula.txt")
        with open(eula_path, 'w') as f:
            f.write("eula=true")

    def run_java_command(self):
        command = f"java -Xms{self.xms} -Xmx{self.xmx} -jar {self.jar_name} nogui"
        start_script = "start.bat" if os.name == 'nt' else "start.sh"
        with open(os.path.join(self.base_dir, start_script), 'w') as f:
            f.write(command)

    def download_file(self, url: str, filename: str):
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(os.path.join(self.base_dir, filename), 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

    def setup_paper(self):
        if not self.version:
            version_resp = requests.get("https://api.papermc.io/v2/projects/paper")
            self.version = version_resp.json()["versions"][-1]
        
        build_resp = requests.get(f"https://api.papermc.io/v2/projects/paper/versions/{self.version}/builds")
        build = build_resp.json()["builds"][-1]["build"]
        url = f"https://api.papermc.io/v2/projects/paper/versions/{self.version}/builds/{build}/downloads/paper-{self.version}-{build}.jar"
        self.download_file(url, self.jar_name)

    def setup_spigot(self):
        buildtools_url = "https://hub.spigotmc.org/jenkins/job/BuildTools/lastSuccessfulBuild/artifact/target/BuildTools.jar"
        self.download_file(buildtools_url, "BuildTools.jar")
        
        version_arg = f"--rev {self.version}" if self.version else ""
        subprocess.run(
            f"java -jar BuildTools.jar {version_arg}",
            cwd=self.base_dir,
            shell=True,
            check=True
        )
        # Rename generated jar
        generated_jar = [f for f in os.listdir(self.base_dir) if f.startswith("spigot-")][0]
        os.rename(
            os.path.join(self.base_dir, generated_jar),
            os.path.join(self.base_dir, self.jar_name)
        )

    def setup_purpur(self):
        if not self.version:
            version_resp = requests.get("https://api.purpurmc.org/v2/purpur")
            self.version = version_resp.json()["versions"][-1]
        
        build_resp = requests.get(f"https://api.purpurmc.org/v2/purpur/{self.version}")
        build = build_resp.json()["builds"]["latest"]
        url = f"https://api.purpurmc.org/v2/purpur/{self.version}/{build}/download"
        self.download_file(url, self.jar_name)

    def setup_glowstone(self):
        self.download_file("https://download.glowstone.net/glowstone.jar", self.jar_name)

    def setup_forge(self):
        installer_url = "https://maven.minecraftforge.net/net/minecraftforge/forge/latest/forge-latest-installer.jar"
        self.download_file(installer_url, "forge-installer.jar")
        subprocess.run(
            "java -jar forge-installer.jar --installServer",
            cwd=self.base_dir,
            shell=True,
            check=True
        )
        # Rename to unified jar name
        for f in os.listdir(self.base_dir):
            if f.startswith("forge-") and f.endswith(".jar") and "universal" in f:
                os.rename(
                    os.path.join(self.base_dir, f),
                    os.path.join(self.base_dir, self.jar_name)
                )

    def install_server(self):
        self.create_directory()
        os.chdir(self.base_dir)
        
        try:
            {
                "paper": self.setup_paper,
                "spigot": self.setup_spigot,
                "purpur": self.setup_purpur,
                "glowstone": self.setup_glowstone,
                "forge": self.setup_forge
            }[self.server_type]()
        except KeyError:
            raise NotImplementedError(f"Server type {self.server_type} not implemented")
        
        self.write_eula()
        self.run_java_command()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Minecraft Server Installer")
        self.setMinimumSize(600, 400)
        
        self.thread = None
        self.worker = None
        
        self.init_ui()
    
    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout()
        
        # Server Type
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Server Type:"))
        self.server_type = QComboBox()
        self.server_type.addItems(["Paper", "Spigot", "Purpur", "Glowstone", "Forge"])
        type_layout.addWidget(self.server_type)
        
        # Version
        version_layout = QHBoxLayout()
        version_layout.addWidget(QLabel("Version (leave empty for latest):"))
        self.version = QLineEdit()
        version_layout.addWidget(self.version)
        
        # Memory Settings
        memory_layout = QHBoxLayout()
        memory_layout.addWidget(QLabel("Initial Memory (Xms):"))
        self.xms = QLineEdit("1G")
        memory_layout.addWidget(self.xms)
        memory_layout.addWidget(QLabel("Max Memory (Xmx):"))
        self.xmx = QLineEdit("4G")
        memory_layout.addWidget(self.xmx)
        
        # Install Directory
        dir_layout = QHBoxLayout()
        self.dir_label = QLabel("Install Directory: ")
        dir_layout.addWidget(self.dir_label)
        self.dir_button = QPushButton("Choose Folder")
        self.dir_button.clicked.connect(self.choose_directory)
        dir_layout.addWidget(self.dir_button)
        
        # Log Output
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        
        # Install Button
        self.install_btn = QPushButton("Install Server")
        self.install_btn.clicked.connect(self.start_installation)
        
        # Assemble layout
        layout.addLayout(type_layout)
        layout.addLayout(version_layout)
        layout.addLayout(memory_layout)
        layout.addLayout(dir_layout)
        layout.addWidget(self.log)
        layout.addWidget(self.install_btn)
        
        central_widget.setLayout(layout)
    
    def choose_directory(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Installation Directory")
        if dir_path:
            self.dir_label.setText(f"Install Directory: {dir_path}")
    
    def start_installation(self):
        config = {
            'type': self.server_type.currentText(),
            'version': self.version.text() or None,
            'xms': self.xms.text(),
            'xmx': self.xmx.text(),
            'install_dir': self.dir_label.text().replace("Install Directory: ", "") or None
        }
        
        if not config['install_dir']:
            QMessageBox.warning(self, "Warning", "Please select an installation directory")
            return
        
        self.install_btn.setEnabled(False)
        self.log.clear()
        
        # Setup thread and worker
        self.thread = QThread()
        self.worker = InstallerWorker(config)
        self.worker.moveToThread(self.thread)
        
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.update_log)
        self.worker.finished.connect(self.installation_finished)
        self.worker.error.connect(self.handle_error)
        
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()
    
    def update_log(self, message):
        self.log.append(message)
    
    def installation_finished(self, success, message):
        self.thread.quit()
        self.thread.wait()
        self.install_btn.setEnabled(True)
        status = "Success" if success else "Failed"
        self.log.append(f"\n{status}: {message}")
        if success:
            QMessageBox.information(self, "Success", "Server installed successfully!")
    
    def handle_error(self, message):
        self.log.append(f"<font color='red'>{message}</font>")
        QMessageBox.critical(self, "Error", message)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())